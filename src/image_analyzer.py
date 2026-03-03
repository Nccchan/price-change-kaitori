"""
Claude Vision API を使って競合価格表画像を解析するモジュール
"""
import base64
import json
import os
import re
from pathlib import Path
from typing import Optional

import anthropic

from src.models import CardItem, CompetitorData, CompetitorType, GameType
from src.config import COMPETITOR_NAMES, PRICE_LABELS


SYSTEM_PROMPT = """あなたはトレーディングカード買取価格表の画像解析専門家です。
競合他社の価格表画像を解析し、全商品の価格データを正確に抽出してください。

## 抽出ルール
1. 画像内の全商品を漏れなく抽出すること
2. 価格は整数（円）で抽出すること（¥記号・カンマは除去）
3. 判読不能な価格は null とすること
4. 商品名・型式は画像内の表記をそのまま使用すること

## 競合識別
- ほむら東京: 「買取価格表」+ LINE homura_tokyo, 黄色/オレンジ背景
- マッチョ買取: 緑色ヘッダー, ピンク系ドラゴンボール

## カードゲーム識別
- ポケモン: シュリンク有/無、SV/S/SMシリーズ型式
- ワンピース: OP-XX / PRB-XX / EB-XX 型式
- ドラゴンボール: FB-XX / SB-XX 型式

## 出力形式（JSON のみ、前後に余計なテキスト不要）
{
  "competitor": "homura_tokyo" | "macho_kaitori" | "other",
  "game": "pokemon" | "onepiece" | "dragonball" | "yugioh",
  "date": "解析日付 (YYYY/MM/DD 形式、不明なら null)",
  "items": [
    {
      "name": "商品名（日本語）",
      "code": "型式（例：SV9, OP-10, FB-03）",
      "price_1": 10000,
      "price_2": 120000
    }
  ]
}

## 価格フィールドの意味
- ポケモン: price_1=シュリンクあり価格, price_2=シュリンクなし価格
- ワンピース/ドラゴンボール: price_1=BOX価格, price_2=カートン価格
"""


class ImageAnalyzer:
    """競合価格表画像を Claude Vision で解析するクラス"""

    def __init__(self, api_key: Optional[str] = None):
        self.client = anthropic.Anthropic(
            api_key=api_key or os.getenv("ANTHROPIC_API_KEY")
        )

    def analyze(
        self,
        image_path: str,
        game_hint: Optional[str] = None,
        competitor_hint: Optional[str] = None,
        date_hint: Optional[str] = None,
    ) -> CompetitorData:
        """
        画像ファイルを解析して CompetitorData を返す。

        Args:
            image_path: 画像ファイルパス（ローカル）または URL
            game_hint: カードゲーム種別ヒント（指定時は解析結果を上書き）
            competitor_hint: 競合名ヒント（指定時は解析結果を上書き）
            date_hint: 日付ヒント（指定時は解析結果を上書き）
        """
        print(f"  画像を解析中: {image_path}")
        image_content = self._load_image(image_path)

        user_message_text = "この価格表画像を解析して、全商品の価格データを JSON 形式で抽出してください。"
        if game_hint:
            user_message_text += f"\nカードゲーム: {game_hint}"
        if competitor_hint:
            user_message_text += f"\n競合: {competitor_hint}"
        if date_hint:
            user_message_text += f"\n日付: {date_hint}"

        message = self.client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        image_content,
                        {"type": "text", "text": user_message_text},
                    ],
                }
            ],
        )

        raw = message.content[0].text
        data = self._parse_response(raw)
        return self._build_competitor_data(data, game_hint, competitor_hint, date_hint)

    def analyze_multiple(
        self,
        image_paths: list[str],
        game_hint: Optional[str] = None,
        competitor_hint: Optional[str] = None,
        date_hint: Optional[str] = None,
    ) -> CompetitorData:
        """
        複数画像を解析してマージした CompetitorData を返す。
        競合・ゲーム種別は最初の画像の解析結果を使用する。
        """
        results = []
        for path in image_paths:
            result = self.analyze(path, game_hint, competitor_hint, date_hint)
            results.append(result)

        if not results:
            raise ValueError("画像が指定されていません")

        # 最初の結果をベースにアイテムをマージ（重複は後の画像で上書き）
        merged = results[0]
        seen = {(item.name, item.code) for item in merged.items}
        for result in results[1:]:
            for item in result.items:
                key = (item.name, item.code)
                if key not in seen:
                    merged.items.append(item)
                    seen.add(key)
        return merged

    # ------------------------------------------------------------------
    # 内部メソッド
    # ------------------------------------------------------------------

    def _load_image(self, image_path: str) -> dict:
        """画像パスを anthropic コンテンツブロックに変換する"""
        if image_path.startswith("http://") or image_path.startswith("https://"):
            return {"type": "image", "source": {"type": "url", "url": image_path}}

        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"画像ファイルが見つかりません: {image_path}")

        suffix = path.suffix.lower()
        media_type_map = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }
        media_type = media_type_map.get(suffix, "image/jpeg")
        with open(path, "rb") as f:
            data = base64.standard_b64encode(f.read()).decode("utf-8")

        return {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": data},
        }

    def _parse_response(self, raw: str) -> dict:
        """Claude の応答テキストから JSON を抽出してパース"""
        # コードブロック除去
        raw = re.sub(r"```(?:json)?\s*", "", raw).strip()
        raw = re.sub(r"```\s*$", "", raw).strip()

        # JSON 部分を抽出
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            raise ValueError(f"JSON が見つかりませんでした: {raw[:200]}")

        try:
            return json.loads(match.group())
        except json.JSONDecodeError as e:
            raise ValueError(f"JSON パースエラー: {e}\n応答: {raw[:300]}")

    def _build_competitor_data(
        self,
        data: dict,
        game_hint: Optional[str],
        competitor_hint: Optional[str],
        date_hint: Optional[str],
    ) -> CompetitorData:
        # ゲーム種別
        if game_hint:
            game = GameType.from_str(game_hint)
        else:
            game = GameType.from_str(data.get("game", ""))

        # 競合
        if competitor_hint:
            competitor = CompetitorType.from_str(competitor_hint)
        else:
            competitor = CompetitorType.from_str(data.get("competitor", "other"))

        # 日付
        date = date_hint or data.get("date") or ""

        # アイテム
        items: list[CardItem] = []
        for item_data in data.get("items", []):
            price_1 = item_data.get("price_1")
            price_2 = item_data.get("price_2")
            items.append(
                CardItem(
                    name=item_data.get("name", ""),
                    code=item_data.get("code", ""),
                    price_1=int(price_1) if price_1 is not None else None,
                    price_2=int(price_2) if price_2 is not None else None,
                )
            )

        print(f"  → 競合: {COMPETITOR_NAMES.get(competitor.value, competitor.value)}")
        print(f"  → ゲーム: {game.value}")
        print(f"  → 抽出件数: {len(items)} 商品")

        return CompetitorData(
            game=game,
            competitor=competitor,
            date=date,
            items=items,
        )
