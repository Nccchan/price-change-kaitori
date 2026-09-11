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
from src.supabase_resolver import HomuraSupabaseResolver, normalize_ref
from src.collection_evidence import observed_fetch, observe


class HomuraFetcher:
    BASE_URL = "https://kaitori-homura.com"

    # 公開トレカカテゴリの棚卸し。mode=auto のみ標準価格更新へ流す。
    CATEGORY_REGISTRY = {
        128: {"name": "pokemon_box", "game": "pokemon", "unit": "BOX", "mode": "auto"},
        129: {"name": "pokemon_ns", "game": "pokemon", "unit": "NS", "mode": "auto"},
        130: {"name": "pokemon_special", "game": "pokemon", "unit": "MIXED", "mode": "monitor"},
        131: {"name": "pokemon_carton", "game": "pokemon", "unit": "CARTON", "mode": "monitor"},
        183: {"name": "loose_pack", "game": "pokemon", "unit": "PACK", "mode": "monitor"},
        132: {"name": "onepiece_box", "game": "onepiece", "unit": "BOX", "mode": "auto"},
        133: {"name": "onepiece_carton", "game": "onepiece", "unit": "CARTON", "mode": "auto"},
        160: {"name": "onepiece_other", "game": "onepiece", "unit": "MIXED", "mode": "monitor"},
        159: {"name": "yugioh_box", "game": "yugioh", "unit": "BOX", "mode": "monitor"},
        172: {"name": "yugioh_carton", "game": "yugioh", "unit": "CARTON", "mode": "monitor"},
        157: {"name": "single_card", "game": None, "unit": "SINGLE", "mode": "manual"},
        171: {"name": "dragonball_box", "game": "dragonball", "unit": "BOX", "mode": "auto"},
    }

    # ゲーム × price種別 → サブカテゴリID
    # price_1: シュリンクあり (pokemon) / BOX (others)
    # price_2: シュリンクなし (pokemon) / カートン (others)
    CATEGORY_IDS: Dict[GameType, Dict[str, Optional[int]]] = {
        # price_1_extra: price_1 と同じ扱い(=BOX/シュリンクあり相当)で追加取得するサブカテゴリ。
        # ポケモンの 130 = スペシャル/プロモ枠（25thプロモパック等の単品物）。
        # ID 130 は単位混在のため price_1_extra に入れない。監視専用。
        GameType.POKEMON:    {"price_1": 128, "price_2": 129},
        GameType.ONEPIECE:   {"price_1": 132, "price_2": 133},
        GameType.DRAGONBALL: {"price_1": 171, "price_2": None},  # カートンIDは未確認
        GameType.YUGIOH:     {"price_1": 159, "price_2": 172},
    }

    # 「その他」＝弾コードの無い商品棚（T-348 / work-log 2026-09-02）。
    # price_1/price_2 のペア構造ではなく単一価格の商品リストなので、
    # 通常の BOX/カートン合流（_merge）には流さず fetch_other_raw / resolve_other_items 専用経路で扱う。
    # 2026-09-05 時点でホムラのサイトナビに存在確認できたのは pokemon=130・onepiece=160 のみ。
    # dragonball / yugioh には同種の「その他」カテゴリがサイト上に見当たらない（要継続確認）。
    OTHER_CATEGORY_IDS: Dict[GameType, List[int]] = {
        GameType.POKEMON: [130],      # スペシャルセット
        GameType.ONEPIECE: [160],     # ワンピースカード その他
        GameType.DRAGONBALL: [],      # 未確認（2026-09-05時点でサイト上に専用カテゴリ無し）
        GameType.YUGIOH: [],          # 未確認（同上）
    }

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.trust_env = False  # 環境プロキシをバイパスして直接接続
        self.session.headers["User-Agent"] = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )

    @observed_fetch
    def fetch(self, game: GameType, today: Optional[str] = None) -> CompetitorData:
        """ホムラのウェブサイトから指定ゲームの価格を取得して CompetitorData を返す"""
        cat_ids = self.CATEGORY_IDS.get(game, {})
        cat_1 = cat_ids.get("price_1")
        cat_2 = cat_ids.get("price_2")

        items_1 = self._fetch_category(cat_1) if cat_1 else []
        items_2 = self._fetch_category(cat_2) if cat_2 else []

        # price_1_extra: スペシャル/プロモ枠を price_1 相当として合流。
        for ec in (cat_ids.get("price_1_extra") or []):
            items_1 += self._fetch_category(ec)

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
        observe(self, "category_started", category_id, self.CATEGORY_REGISTRY.get(category_id, {}))
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

            observe(self, "page_received", f"{url}?q[product_sub_category_id_eq]={category_id}&page={page}", resp.text)
            items = self._parse_page(resp.text)
            if not items:
                observe(self, "page_finished", True)
                break
            results.extend(items)

            # 次ページリンクがなければ終了
            soup = BeautifulSoup(resp.text, "html.parser")
            next_link = (
                soup.find("a", rel="next")
                or soup.find("a", string=re.compile(r"次|next", re.I))
                or soup.find("li", class_=re.compile(r"next"))
            )
            observe(self, "page_finished", not bool(next_link))
            if not next_link:
                break
            page += 1

        observe(self, "category_finished")
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

        cards = soup.find_all("h5")
        observe(self, "card_count", len(cards))
        for h5 in cards:
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
            price_text = ""
            for span in card_div.find_all("span"):
                text = span.get_text(strip=True)
                m = re.search(r"¥\s*([\d,]+)", text)
                if m:
                    price = int(m.group(1).replace(",", ""))
                    price_text = text
                    break

            observe(self, "candidate", raw_name, name, code_text,
                    requests.compat.urljoin(self.BASE_URL, link.get("href", "")), price, price_text)
            if price is not None:
                items.append({"name": name, "raw_code": code_text, "price": price})

        return items

    def _clean_name(self, name: str) -> str:
        """商品名から余分なプレフィックス（「BOX」など）と末尾の※注釈を除去。
        例: 'ポケモンカード Classic ※輸送箱未開封' → 'ポケモンカード Classic'
        （※注釈があると②買取価格表の行名と照合できず価格が更新されない）"""
        # BOX/NS等の状態プレフィクスを除去。スペシャルセット等は別カテゴリなので
        # 標準BOX/NS結合には流れず、この除去による単位混入は起きない。
        name = re.sub(r"^[「【〖].*?[」】〗]\s*", "", name)
        name = re.sub(r"\s*※.*$", "", name)
        return name.strip()

    def fetch_all_category_inventory(self) -> Dict[int, List[Dict]]:
        """全公開トレカカテゴリを取得する。書込は行わず監視・監査に使用する。"""
        return {
            category_id: self._fetch_category(category_id)
            for category_id, meta in self.CATEGORY_REGISTRY.items()
            if meta["mode"] != "manual"
        }

    def fetch_other_raw(self, game: GameType) -> List[Dict]:
        """「その他」カテゴリ（弾コード無し商品）の生データを取得する。書込は一切行わない。

        ホムラのページは同一商品の h5 がレスポンシブ用に二重にマークアップされているため
        (name, price) の組でここで重複除去してから返す。
        """
        cat_ids = self.OTHER_CATEGORY_IDS.get(game, [])
        raw: List[Dict] = []
        for cid in cat_ids:
            raw.extend(self._fetch_category(cid))

        seen: set = set()
        deduped: List[Dict] = []
        for it in raw:
            name = self._clean_name(it["name"])
            key = (self._normalize(name), it["price"])
            if key in seen:
                continue
            seen.add(key)
            deduped.append({**it, "name": name})
        return deduped

    def resolve_other_item(self, item: Dict, resolver: HomuraSupabaseResolver) -> Dict:
        """「その他」商品1件を2段で解決する（部分一致・推測はしない）。

        1) 名前先頭の型式コード（例 "OP-01 ..."）があれば resolver.resolve(code) で完全一致
        2) 無ければ表示名を正規化して products.homura_ref と完全一致

        戻り値: {"name", "raw_code", "price", "method": "code"|"name"|None,
                 "normalized_name", "matched": bool, "products": [...]}
        """
        name = item["name"]
        inline_code, _short_name = self._extract_inline_code(name)
        normalized_name = normalize_ref(name)

        if inline_code:
            rows = resolver.resolve(inline_code)
            if rows:
                return {
                    **item,
                    "method": "code",
                    "matched_code": inline_code,
                    "normalized_name": normalized_name,
                    "matched": True,
                    "products": rows,
                }

        rows = resolver.resolve_by_display_name(name)
        if rows:
            return {
                **item,
                "method": "name",
                "matched_code": None,
                "normalized_name": normalized_name,
                "matched": True,
                "products": rows,
            }

        return {
            **item,
            "method": None,
            "matched_code": None,
            "normalized_name": normalized_name,
            "matched": False,
            "products": [],
        }

    def resolve_other_items(
        self, game: GameType, resolver: HomuraSupabaseResolver
    ) -> List[Dict]:
        """fetch_other_raw() の結果をまとめて2段解決する。report-only。"""
        return [self.resolve_other_item(it, resolver) for it in self.fetch_other_raw(game)]

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

        observe(self, "resolved_candidates", resolve, master,
                self._normalize, self._extract_inline_code)
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
