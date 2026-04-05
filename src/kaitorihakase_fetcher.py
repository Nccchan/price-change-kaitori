"""
買取博士ウェブサイトからの価格自動取得モジュール

kaitorihakase.com/client/price-master の Next.js RSCペイロードを解析して
CompetitorData を返す。
"""
import json
import re
import unicodedata
from datetime import date
from typing import Dict, List, Optional, Tuple

import requests

from src.config import get_master_data
from src.models import CardItem, CompetitorData, CompetitorType, GameType


class KaitorihakaseFetcher:
    URL = "https://kaitorihakase.com/client/price-master"

    CATEGORY_MAP = {
        GameType.POKEMON: "pokemon",
        GameType.ONEPIECE: "onepiece",
        GameType.DRAGONBALL: "dragonball",
        GameType.YUGIOH: "yugioh",
    }

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers["User-Agent"] = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )

    def fetch(self, game: GameType, today: Optional[str] = None) -> CompetitorData:
        """買取博士から指定ゲームの価格を取得して CompetitorData を返す"""
        resp = self.session.get(self.URL, timeout=30)
        resp.raise_for_status()

        all_items = self._extract_items(resp.text)
        category = self.CATEGORY_MAP[game]
        cat_items = [i for i in all_items if i.get("category") == category]

        # デバッグ: 利用可能なタイプを出力
        available_types = sorted(set(i.get("type", "?") for i in cat_items))
        print(f"  [DEBUG] {game.value} 利用可能タイプ: {available_types}")

        if game == GameType.POKEMON:
            # シュリンクあり / シュリンクなし
            p1_items = [i for i in cat_items
                        if i.get("type") == "box" and i.get("sealing") == "shrink_on"]
            p2_items = [i for i in cat_items
                        if i.get("type") == "box" and i.get("sealing") == "shrink_off"]
            p1_fallback_items = []
        else:
            # BOX / カートン
            p1_items = [i for i in cat_items if i.get("type") == "box"]
            p2_items = [i for i in cat_items if i.get("type") == "carton"]
            # BOX買取非掲載の商品用フォールバック: パック（バック）単価を利用
            # 博士がBOX単位で募集していない場合、パック価格を本の価格として使用
            p1_fallback_items = [i for i in cat_items
                                 if i.get("type") in ("pack", "back", "hon", "パック")]

        merged = self._merge(p1_items, p2_items, game, p1_fallback_items)
        today_str = today or date.today().strftime("%Y/%m/%d")

        return CompetitorData(
            game=game,
            competitor=CompetitorType.KAITORIHAKASE,
            date=today_str,
            items=merged,
        )

    def _extract_items(self, html: str) -> List[dict]:
        """Next.js RSCペイロード（self.__next_f.push）からitemsを抽出"""
        # HTMLには \"items\":[ の形でJSONがエスケープ埋め込みされている
        marker = '\\"items\\":'
        pos = html.find(marker)
        if pos == -1:
            return []

        bracket_start = html.find('[', pos + len(marker))
        if bracket_start == -1:
            return []

        # ブラケット深度を追跡して対応する ] を見つける
        # バックスラッシュエスケープを考慮してスキップ
        depth = 0
        i = bracket_start
        end = bracket_start
        while i < len(html):
            c = html[i]
            if c == '\\':
                i += 2  # エスケープシーケンスをスキップ
                continue
            if c in '[{':
                depth += 1
            elif c in ']}':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
            i += 1

        escaped_json = html[bracket_start:end]
        try:
            # HTML内のエスケープ済みJSON文字列としてデコード
            # \"...\" → "..."  になるよう JSONの文字列値として解釈
            decoded_str = json.loads('"' + escaped_json + '"')
            return json.loads(decoded_str)
        except (json.JSONDecodeError, ValueError):
            # フォールバック: 単純なエスケープ解除
            try:
                unescaped = escaped_json.replace('\\"', '"').replace('\\\\', '\\')
                return json.loads(unescaped)
            except json.JSONDecodeError:
                return []

    def _normalize(self, s: str) -> str:
        """比較用に正規化（全角→半角、小文字化、空白除去）"""
        return unicodedata.normalize("NFKC", s).lower().strip()

    def _resolve_code(
        self,
        item: dict,
        master: dict,
        name_to_code: Dict[str, str],
    ) -> Tuple[str, str]:
        """(code, display_name) を返す"""
        product_name = item.get("productName", "")
        edition = item.get("edition", "")

        # 1. editionフィールド: OP-02, FB-01 等の形式
        if edition:
            # FB01 → FB-01, SB01 → SB-01 のような形式を正規化
            norm_ed = re.sub(r'^([A-Z]+)(\d{2}[a-z]?)$', r'\1-\2', edition.strip())
            if norm_ed in master:
                # マスターの正規名を使うことでスプレッドシートの名前と一致させる
                return norm_ed, master[norm_ed]

        # 2. 商品名の先頭にコードが含まれている場合 (OP-02 頂上決戦, FB01 覚醒の鼓動)
        m = re.match(r'^([A-Z]+-\d{2}[a-z]?)\s+(.*)', product_name)
        if m and m.group(1) in master:
            return m.group(1), master[m.group(1)]

        # ハイフンなし: FB01 → FB-01
        m2 = re.match(r'^([A-Z]+)(\d{2}[a-z]?)\s+(.*)', product_name)
        if m2:
            norm_code = m2.group(1) + '-' + m2.group(2)
            if norm_code in master:
                return norm_code, master[norm_code]

        # 3. マスターデータの名前で完全一致
        normalized = self._normalize(product_name)
        if normalized in name_to_code:
            return name_to_code[normalized], product_name

        # 4. 部分一致（例: "151" → "ポケモンカード151"）
        for master_name_norm, code in name_to_code.items():
            if normalized and master_name_norm.endswith(normalized):
                return code, master.get(code, product_name)

        # 5. フォールバック
        return product_name, product_name

    def _merge(
        self,
        p1_items: List[dict],
        p2_items: List[dict],
        game: GameType,
        p1_fallback_items: Optional[List[dict]] = None,
    ) -> List[CardItem]:
        master = get_master_data(game.value)  # {code: name}
        name_to_code: Dict[str, str] = {
            self._normalize(name): code for code, name in master.items()
        }

        def to_map(items: List[dict]) -> Dict[str, dict]:
            result: Dict[str, dict] = {}
            for item in items:
                code, name = self._resolve_code(item, master, name_to_code)
                key = self._normalize(code)
                result[key] = {"name": name, "code": code, "price": item["price"]}
            return result

        p1_map = to_map(p1_items)
        p2_map = to_map(p2_items)
        p1_fallback_map = to_map(p1_fallback_items) if p1_fallback_items else {}
        all_keys = set(p1_map) | set(p2_map) | set(p1_fallback_map)

        result = []
        for key in sorted(all_keys):
            e1 = p1_map.get(key)
            # BOX価格がない場合はパック（バック）価格をフォールバックとして使用
            if e1 is None and key in p1_fallback_map:
                e1 = p1_fallback_map[key]
                print(f"  [INFO] {e1['code']}: BOX非掲載のためパック価格を使用 ¥{e1['price']:,}")
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
