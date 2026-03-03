"""
現行価格と競合価格を比較してマージン状態を評価するモジュール
"""
from typing import List, Optional, Dict, Tuple

from src.config import MARGINS, PRICE_LABELS
from src.models import CardItem, CompetitorData, ComparisonResult, UpdatePayload


def _normalize_key(name: str, code: str) -> str:
    """商品の照合キーを生成（名前と型式の組み合わせ）"""
    return f"{name.strip()}|{code.strip()}"


def _find_match(
    competitor_item: CardItem,
    current_items: List[CardItem],
) -> Optional[CardItem]:
    """
    競合データのアイテムに対応する現行シートのアイテムを探す。
    優先順位: (1) 商品名+型式 完全一致 → (2) 型式 一致 → (3) 商品名 一致
    """
    # (1) 両方一致
    for item in current_items:
        if item.name == competitor_item.name and item.code == competitor_item.code:
            return item

    # (2) 型式が一致（空でない場合のみ）
    if competitor_item.code:
        for item in current_items:
            if item.code and item.code.strip() == competitor_item.code.strip():
                return item

    # (3) 商品名が一致
    if competitor_item.name:
        for item in current_items:
            if item.name and item.name.strip() == competitor_item.name.strip():
                return item

    return None


class PriceComparator:
    """現行価格と競合価格の比較ロジック"""

    def __init__(self, custom_margins: Optional[Dict[str, int]] = None):
        """
        Args:
            custom_margins: {"box": N, "carton": M} 形式のマージン上書き
        """
        self.custom_margins = custom_margins or {}

    def compare(
        self,
        game: str,
        competitor_data: CompetitorData,
        current_items: List[CardItem],
    ) -> List[ComparisonResult]:
        """
        競合データと現行データを比較して ComparisonResult リストを返す。
        競合データに存在する全商品が対象となる。
        """
        margin_box = self.custom_margins.get(
            "box", MARGINS.get(game, {}).get("box", 200)
        )
        margin_carton = self.custom_margins.get(
            "carton", MARGINS.get(game, {}).get("carton", 200)
        )

        results: List[ComparisonResult] = []

        for comp_item in competitor_data.items:
            current = _find_match(comp_item, current_items)
            is_new = current is None

            results.append(
                ComparisonResult(
                    name=comp_item.name,
                    code=comp_item.code,
                    competitor_price_1=comp_item.price_1,
                    competitor_price_2=comp_item.price_2,
                    current_price_1=current.price_1 if current else None,
                    current_price_2=current.price_2 if current else None,
                    required_margin_1=margin_box,
                    required_margin_2=margin_carton,
                    is_new=is_new,
                )
            )

        return results

    def build_update_payloads(
        self,
        game: str,
        comparison_results: List[ComparisonResult],
        current_items: List[CardItem],
        next_available_row: int,
    ) -> List[UpdatePayload]:
        """
        スプレッドシート更新用のペイロードリストを生成する。
        全アイテムについて推奨価格（競合 + マージン）を設定する。
        新商品は末尾行に追加する。
        """
        payloads: List[UpdatePayload] = []
        new_row = next_available_row

        for result in comparison_results:
            # 対応する現行アイテムを再検索してrow_indexを取得
            current = _find_match(
                CardItem(name=result.name, code=result.code),
                current_items,
            )

            if current is not None:
                row_idx = current.row_index
            else:
                # 新商品: 末尾に追加
                row_idx = new_row
                new_row += 1

            payloads.append(
                UpdatePayload(
                    row_index=row_idx,
                    name=result.name,
                    code=result.code,
                    new_price_1=result.recommended_price_1,
                    new_price_2=result.recommended_price_2,
                    is_new=(current is None),
                )
            )

        return payloads

    def get_attention_items(
        self, results: List[ComparisonResult]
    ) -> List[ComparisonResult]:
        """対応が必要なアイテム（マージン不足・新商品）を抽出"""
        return [r for r in results if r.needs_attention]

    def get_large_changes(
        self, results: List[ComparisonResult]
    ) -> List[ComparisonResult]:
        """大幅な価格変動（閾値以上）があるアイテムを抽出"""
        return [r for r in results if r.is_large_change_1 or r.is_large_change_2]
