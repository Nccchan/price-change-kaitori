"""
pokeca-box-hikaku.com からポケカ最高買取価格を取得するモジュール

全8店舗の買取価格のうち最高値を price_1（BOX価格）として返す。
price_2（カートン）はサイト非対応のため常に None。
対応ゲーム: ポケモンのみ
"""
import json
import re
import unicodedata
from datetime import date
from typing import Dict, List, Optional, Tuple

import requests

from src.config import get_master_data
from src.models import CardItem, CompetitorData, CompetitorType, GameType


class PokecaFetcher:
    URL = "https://pokeca-box-hikaku.com/"

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers["User-Agent"] = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )

    def fetch(self, game: GameType, today: Optional[str] = None) -> CompetitorData:
        """pokeca-box-hikaku.com からポケカ最高買取価格を取得して CompetitorData を返す"""
        if game != GameType.POKEMON:
            raise ValueError(
                f"PokecaFetcher はポケモンのみ対応しています（指定: {game.value}）"
            )

        resp = self.session.get(self.URL, timeout=30)
        resp.raise_for_status()

        raw_items = self._extract_items(resp.text)
        print(f"  [DEBUG] サイトアイテム数: {len(raw_items)}")

        master = get_master_data("pokemon")  # code → name
        name_to_code: Dict[str, str] = {
            self._normalize(name): code for code, name in master.items()
        }

        card_items = []
        seen_codes: Dict[str, int] = {}  # code → max_price (重複排除)

        for item in raw_items:
            site_name = item.get("n", "")
            prices = item.get("p", {})

            # 全店舗価格のうち 0 より大きいものの最高値
            valid_prices = [
                p for p in prices.values()
                if isinstance(p, (int, float)) and p > 0
            ]
            if not valid_prices:
                continue
            max_price = int(max(valid_prices))

            # 商品名をマスターコードに解決
            code, name = self._resolve_code(site_name, master, name_to_code)

            norm_code = self._normalize(code)
            if norm_code in seen_codes:
                # 同コードが複数ある場合は最高値を採用
                if max_price > seen_codes[norm_code]:
                    seen_codes[norm_code] = max_price
                    # 既存エントリを上書き
                    for ci in card_items:
                        if self._normalize(ci.code) == norm_code:
                            ci.price_1 = max_price
                            break
            else:
                seen_codes[norm_code] = max_price
                card_items.append(CardItem(
                    name=name,
                    code=code,
                    price_1=max_price,
                    price_2=None,
                ))

        today_str = today or date.today().strftime("%Y/%m/%d")
        print(f"  [DEBUG] マッピング済みアイテム数: {len(card_items)}")

        return CompetitorData(
            game=game,
            competitor=CompetitorType.POKECA_MAX,
            date=today_str,
            items=card_items,
        )

    def _extract_items(self, html: str) -> List[dict]:
        """HTML から const P = [...] を抽出して解析する"""
        # const P = [...] を探す（末尾セミコロンあり・なし両対応）
        m = re.search(r'const\s+P\s*=\s*(\[.+?\]);', html, re.DOTALL)
        if not m:
            m = re.search(r'const\s+P\s*=\s*(\[.+\])\s*(?:;|$)', html, re.DOTALL)
        if not m:
            print("  [WARN] const P = [...] が見つかりませんでした")
            return []

        js_array = m.group(1)

        # JS オブジェクトの未クォートキーをクォートして JSON 化
        # 例: morimori: 35500 → "morimori": 35500
        json_str = re.sub(r'(?<!["\'\w])([A-Za-z_]\w*)\s*:', r'"\1":', js_array)

        # JS の末尾カンマ（trailing comma）を除去
        json_str = re.sub(r',\s*([}\]])', r'\1', json_str)

        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            print(f"  [WARN] JSON パース失敗: {e}")
            return []

    def _normalize(self, s: str) -> str:
        """比較用に正規化（全角→半角、小文字化、空白除去）"""
        return unicodedata.normalize("NFKC", s).lower().strip()

    def _resolve_code(
        self,
        site_name: str,
        master: dict,
        name_to_code: Dict[str, str],
    ) -> Tuple[str, str]:
        """サイトの商品名（n フィールド）をマスターコードと商品名に解決する"""
        # 「」内の商品名を抽出（例: "SV 拡張パック「超電ブレイカー」" → "超電ブレイカー"）
        bracket_m = re.search(r'「(.+?)」', site_name)
        bracket_name = bracket_m.group(1) if bracket_m else site_name
        normalized = self._normalize(bracket_name)

        # 1. 完全一致
        if normalized in name_to_code:
            code = name_to_code[normalized]
            return code, master.get(code, bracket_name)

        # 2. 末尾一致（"151" → "ポケモンカード151"）
        for master_norm, code in name_to_code.items():
            if normalized and master_norm.endswith(normalized):
                return code, master.get(code, bracket_name)

        # 3. 部分一致（サイト名がマスター名に含まれる）
        for master_norm, code in name_to_code.items():
            if normalized and normalized in master_norm:
                return code, master.get(code, bracket_name)

        # フォールバック: サイト名をそのまま使用
        return bracket_name, bracket_name

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
