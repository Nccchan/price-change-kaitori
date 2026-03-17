"""
ほむら東京ウェブサイトからの価格自動取得モジュール

kaitori-homura.com の商品ページをスクレイピングして CompetitorData を返す。
"""
import re
import unicodedata
from datetime import date
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup

from src.config import get_master_data
from src.models import CardItem, CompetitorData, CompetitorType, GameType


class HomuraFetcher:
    BASE_URL = "https://kaitori-homura.com"

    # ゲーム × price種別 → サブカテゴリID
    # price_1: シュリンクあり (pokemon) / BOX (others)
    # price_2: シュリンクなし (pokemon) / カートン (others)
    CATEGORY_IDS: Dict[GameType, Dict[str, Optional[int]]] = {
        GameType.POKEMON:    {"price_1": 128, "price_2": 129},
        GameType.ONEPIECE:   {"price_1": 132, "price_2": 133},
        GameType.DRAGONBALL: {"price_1": 171, "price_2": None},  # カートンIDは未確認
        GameType.YUGIOH:     {"price_1": 159, "price_2": 172},
    }

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers["User-Agent"] = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )

    def fetch(self, game: GameType, today: Optional[str] = None) -> CompetitorData:
        """ホムラのウェブサイトから指定ゲームの価格を取得して CompetitorData を返す"""
        cat_ids = self.CATEGORY_IDS.get(game, {})
        cat_1 = cat_ids.get("price_1")
        cat_2 = cat_ids.get("price_2")

        items_1 = self._fetch_category(cat_1) if cat_1 else []
        items_2 = self._fetch_category(cat_2) if cat_2 else []

        merged = self._merge(items_1, items_2, game)
        today_str = today or date.today().strftime("%Y/%m/%d")

        return CompetitorData(
            game=game,
            competitor=CompetitorType.HOMURA,
            date=today_str,
            items=merged,
        )

    def _fetch_category(self, category_id: int) -> List[Dict]:
        """指定カテゴリの全ページから商品リストを取得"""
        results = []
        page = 1
        while True:
            url = f"{self.BASE_URL}/products"
            params = {
                "q[product_sub_category_id_eq]": category_id,
                "page": page,
            }
            resp = self.session.get(url, params=params, timeout=20)
            resp.raise_for_status()

            items = self._parse_page(resp.text)
            if not items:
                break
            results.extend(items)

            # 次ページリンクがなければ終了
            soup = BeautifulSoup(resp.text, "html.parser")
            next_link = (
                soup.find("a", rel="next")
                or soup.find("a", string=re.compile(r"次|next", re.I))
                or soup.find("li", class_=re.compile(r"next"))
            )
            if not next_link:
                break
            page += 1

        return results

    def _parse_page(self, html: str) -> List[Dict]:
        """HTMLから商品情報（name, raw_code, price）を抽出。

        実際のHTML構造:
          <div class="flex flex-col md:flex-row ...">  ← カードコンテナ
            <div class="flex gap-x-2 ...">
              <a href="..."><div>画像</div></a>
              <div>
                <a href="..."><h5>商品名</h5></a>
                <span>バーコード</span>
              </div>
            </div>
            <div class="flex shrink-0 ...">   ← 価格エリア
              <span>買取金額（税込）</span>
              <span class="... font-semibold ...">10,300円</span>
            </div>
          </div>
        """
        soup = BeautifulSoup(html, "html.parser")
        items = []

        for h5 in soup.find_all("h5"):
            raw_name = h5.get_text(strip=True)
            name = self._clean_name(raw_name)
            if not name:
                continue

            # h5 → <a> → <div>(名前+コード) → <div>(gap-x-2) → カードコンテナ
            link = h5.find_parent("a")
            if link is None:
                continue
            name_div = link.parent       # <div> containing <a> and code <span>
            gap_div = name_div.parent    # <div class="flex gap-x-2 ...">
            card_div = gap_div.parent    # カードコンテナ（価格も含む）

            # バーコード: <a>の次の<span>
            code_span = link.find_next_sibling("span")
            code_text = code_span.get_text(strip=True) if code_span else ""

            # 価格: カードコンテナ内で "X,XXX円" パターンの<span>を探す
            price: Optional[int] = None
            for span in card_div.find_all("span"):
                text = span.get_text(strip=True)
                m = re.search(r"^([\d,]+)円$", text)
                if m:
                    price = int(m.group(1).replace(",", ""))
                    break

            if price is not None:
                items.append({"name": name, "raw_code": code_text, "price": price})

        return items

    def _clean_name(self, name: str) -> str:
        """商品名から余分なプレフィックス（「BOX」など）を除去"""
        name = re.sub(r"^[「【].*?[」】]\s*", "", name)
        return name.strip()

    def _extract_inline_code(self, name: str) -> tuple[Optional[str], str]:
        """名前の先頭にある型式コード (OP-01, PRB-01, EB-01, FB-01 等) を抽出する。
        例: "OP-01 ロマンスドーン" → ("OP-01", "ロマンスドーン")
        例: "PRB-02ONE PIECE..." → ("PRB-02", "ONE PIECE...")
        コードがなければ → (None, name)
        """
        m = re.match(r"^([A-Z]{1,3}-\d{2}[a-z]?)(?:\s|(?=[A-Z0-9]))(.*)", name, re.DOTALL)
        if m:
            return m.group(1), m.group(2).strip()
        return None, name

    def _normalize(self, s: str) -> str:
        """比較用に正規化（全角→半角、小文字化、空白除去）"""
        return unicodedata.normalize("NFKC", s).lower().strip()

    def _merge(
        self,
        items_1: List[Dict],
        items_2: List[Dict],
        game: GameType,
    ) -> List[CardItem]:
        """price_1 リストと price_2 リストを商品名でマッチして CardItem に結合"""
        # マスターデータから {name -> code} の逆引きマップを作成
        master = get_master_data(game.value)  # {code: name}
        name_to_code: Dict[str, str] = {
            self._normalize(name): code for code, name in master.items()
        }

        def resolve(name: str, raw_code: str) -> tuple[str, str]:
            """(code, display_name) を返す"""
            # 1) 名前の先頭に型式コードが含まれていればそれを使う (OP-01, FB-01 等)
            inline_code, short_name = self._extract_inline_code(name)
            if inline_code:
                return inline_code, short_name
            # 2) マスターデータの名称でルックアップ
            code = name_to_code.get(self._normalize(name), raw_code)
            return code, name

        def to_map(items: List[Dict]) -> Dict[str, Dict]:
            m: Dict[str, Dict] = {}
            for it in items:
                code, display_name = resolve(it["name"], it["raw_code"])
                # マージのキーはコードが取れた場合はコード、なければ名前
                key = self._normalize(code) if code != it["raw_code"] else self._normalize(display_name)
                m[key] = {"name": display_name, "code": code, "price": it["price"]}
            return m

        p1_map = to_map(items_1)
        p2_map = to_map(items_2)

        all_keys = set(p1_map.keys()) | set(p2_map.keys())

        result = []
        for key in sorted(all_keys):
            e1 = p1_map.get(key)
            e2 = p2_map.get(key)
            base = e1 or e2
            result.append(CardItem(
                name=base["name"],
                code=base["code"],
                price_1=e1["price"] if e1 else None,
                price_2=e2["price"] if e2 else None,
            ))

        return result

    def to_json(self, data: CompetitorData) -> dict:
        """CompetitorData を JSON シリアライズ可能な辞書に変換"""
        return {
            "competitor": data.competitor.value,
            "game": data.game.value,
            "date": data.date,
            "items": [
                {
                    "name": it.name,
                    "code": it.code,
                    "price_1": it.price_1,
                    "price_2": it.price_2,
                }
                for it in data.items
            ],
        }
