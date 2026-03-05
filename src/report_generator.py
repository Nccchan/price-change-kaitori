"""
価格比較レポートを生成するモジュール
"""
from typing import List, Optional

from src.config import COMPETITOR_NAMES, PRICE_LABELS, LARGE_CHANGE_THRESHOLD
from src.models import ComparisonResult, CompetitorData, DailyChangeResult, GameType


def _fmt(price: Optional[int]) -> str:
    if price is None:
        return "—"
    return f"¥{price:,}"


def _fmt_diff(diff: Optional[int]) -> str:
    if diff is None:
        return "—"
    if diff > 0:
        return f"+{diff:,}"
    return f"{diff:,}"


class ReportGenerator:
    """Markdown + タブ区切りテキストのレポートを生成するクラス"""

    def generate(
        self,
        game: str,
        competitor_data: CompetitorData,
        results: List[ComparisonResult],
        margin_box: int,
        margin_carton: int,
    ) -> str:
        """
        完全なレポート（Markdown）を生成して返す。
        """
        labels = PRICE_LABELS.get(game, {"price1": "価格1", "price2": "価格2"})
        competitor_name = COMPETITOR_NAMES.get(
            competitor_data.competitor.value, competitor_data.competitor.value
        )
        game_label = {
            "pokemon": "ポケモンカード",
            "onepiece": "ワンピースカード",
            "dragonball": "ドラゴンボールカード",
            "yugioh": "遊戯王カード",
        }.get(game, game)

        date_str = competitor_data.date or "日付不明"
        sections = []

        # 1. 比較表
        sections.append(
            self._comparison_table(
                game_label, competitor_name, date_str, labels, results
            )
        )

        # 2. 要対応商品
        attention = [r for r in results if r.needs_attention]
        sections.append(
            self._attention_section(labels, attention, competitor_name)
        )

        # 3. 大幅変動警告
        large_changes = [
            r for r in results if r.is_large_change_1 or r.is_large_change_2
        ]
        if large_changes:
            sections.append(self._large_change_warning(labels, large_changes))

        # 4. 更新版リスト（コピペ用タブ区切り）
        sections.append(
            self._updated_list(
                game,
                game_label,
                competitor_name,
                date_str,
                labels,
                results,
                margin_box,
                margin_carton,
            )
        )

        return "\n\n".join(sections)

    # ------------------------------------------------------------------
    # セクション生成
    # ------------------------------------------------------------------

    def _comparison_table(
        self,
        game_label: str,
        competitor_name: str,
        date_str: str,
        labels: dict,
        results: List[ComparisonResult],
    ) -> str:
        lines = [
            f"## {game_label} 比較（{date_str}）",
            f"競合: **{competitor_name}**",
            "",
        ]

        # ヘッダー
        p1 = labels["price1"]
        p2 = labels["price2"]
        lines.append(
            f"| 商品名 | 型式 | 競合{p1} | 弊社{p1} | 差額 | 状態 |"
            f" 競合{p2} | 弊社{p2} | 差額 | 状態 |"
        )
        lines.append(
            "|--------|------|" + ("----------|----------|------|------|" * 2)
        )

        for r in results:
            flag = "🆕" if r.is_new else ""
            large_1 = "⚡" if r.is_large_change_1 else ""
            large_2 = "⚡" if r.is_large_change_2 else ""
            lines.append(
                f"| {flag}{r.name} | {r.code} "
                f"| {_fmt(r.competitor_price_1)} "
                f"| {_fmt(r.current_price_1)} "
                f"| {large_1}{_fmt_diff(r.diff_1)} "
                f"| {r.status_1} "
                f"| {_fmt(r.competitor_price_2)} "
                f"| {_fmt(r.current_price_2)} "
                f"| {large_2}{_fmt_diff(r.diff_2)} "
                f"| {r.status_2} |"
            )

        total = len(results)
        ok_1 = sum(1 for r in results if r.status_1 == "✓")
        ok_2 = sum(1 for r in results if r.status_2 == "✓")
        lines.append("")
        lines.append(
            f"**集計**: {total} 商品 /"
            f" {p1}: ✓{ok_1} ⚠️{total - ok_1} /"
            f" {p2}: ✓{ok_2} ⚠️{total - ok_2}"
        )
        return "\n".join(lines)

    def _attention_section(
        self,
        labels: dict,
        attention: List[ComparisonResult],
        competitor_name: str,
    ) -> str:
        if not attention:
            return "## ✅ 要対応商品なし\n全商品のマージンが維持されています。"

        p1 = labels["price1"]
        p2 = labels["price2"]
        lines = [
            f"## ⚠️ 要対応（{len(attention)} 商品）",
            "",
            f"| 商品名 | 型式 | 競合{p1} | 弊社{p1} | 推奨{p1} |"
            f" 競合{p2} | 弊社{p2} | 推奨{p2} |",
            "|--------|------|----------|----------|----------|"
            "----------|----------|----------|",
        ]
        for r in attention:
            flag = "🆕" if r.is_new else ""
            lines.append(
                f"| {flag}{r.name} | {r.code} "
                f"| {_fmt(r.competitor_price_1)} "
                f"| {_fmt(r.current_price_1)} "
                f"| **{_fmt(r.recommended_price_1)}** "
                f"| {_fmt(r.competitor_price_2)} "
                f"| {_fmt(r.current_price_2)} "
                f"| **{_fmt(r.recommended_price_2)}** |"
            )
        return "\n".join(lines)

    def _large_change_warning(
        self, labels: dict, large_changes: List[ComparisonResult]
    ) -> str:
        p1 = labels["price1"]
        p2 = labels["price2"]
        lines = [
            f"## ⚡ 大幅変動注意（±¥{LARGE_CHANGE_THRESHOLD:,}以上）",
            "",
            f"| 商品名 | 型式 | {p1}変動 | {p2}変動 |",
            "|--------|------|---------|---------|",
        ]
        for r in large_changes:
            d1 = _fmt_diff(r.diff_1) if r.is_large_change_1 else "—"
            d2 = _fmt_diff(r.diff_2) if r.is_large_change_2 else "—"
            lines.append(f"| {r.name} | {r.code} | **{d1}** | **{d2}** |")
        return "\n".join(lines)

    def _updated_list(
        self,
        game: str,
        game_label: str,
        competitor_name: str,
        date_str: str,
        labels: dict,
        results: List[ComparisonResult],
        margin_box: int,
        margin_carton: int,
    ) -> str:
        p1 = labels["price1"]
        p2 = labels["price2"]
        margin_note = f"{p1} +{margin_box}円 / {p2} +{margin_carton}円"

        lines = [
            f"## 更新版リスト（{competitor_name} {date_str} {margin_note}）弊社形式",
            "",
            "```",
            f"商品名\t型式\t買取単価（{p1}）\t買取単価（{p2}）",
        ]
        for r in results:
            price_1_str = str(r.recommended_price_1) if r.recommended_price_1 else ""
            price_2_str = str(r.recommended_price_2) if r.recommended_price_2 else ""
            lines.append(f"{r.name}\t{r.code}\t{price_1_str}\t{price_2_str}")
        lines.append("```")
        return "\n".join(lines)

    def generate_change_summary(
        self,
        results: List[ComparisonResult],
        labels: dict,
    ) -> str:
        """前回価格（現行）→ 今回推奨価格 の変更点サマリーを生成"""
        changed = [
            r
            for r in results
            if (
                r.recommended_price_1 is not None
                and r.current_price_1 is not None
                and r.recommended_price_1 != r.current_price_1
            )
            or (
                r.recommended_price_2 is not None
                and r.current_price_2 is not None
                and r.recommended_price_2 != r.current_price_2
            )
        ]

        if not changed:
            return "### 変更点なし\n全商品の価格は変動しませんでした。"

        p1 = labels["price1"]
        p2 = labels["price2"]
        lines = [
            "### 前回からの主な変更点",
            "",
            f"| 商品名 | 型式 | 前回{p1} | 今回{p1} | 変動 | 前回{p2} | 今回{p2} | 変動 |",
            "|--------|------|----------|----------|------|----------|----------|------|",
        ]
        for r in changed:
            def change_arrow(prev, curr):
                if prev is None or curr is None:
                    return "—"
                d = curr - prev
                if d > 0:
                    return f"▲{d:,}"
                elif d < 0:
                    return f"▼{abs(d):,}"
                return "±0"

            lines.append(
                f"| {r.name} | {r.code} "
                f"| {_fmt(r.current_price_1)} "
                f"| {_fmt(r.recommended_price_1)} "
                f"| {change_arrow(r.current_price_1, r.recommended_price_1)} "
                f"| {_fmt(r.current_price_2)} "
                f"| {_fmt(r.recommended_price_2)} "
                f"| {change_arrow(r.current_price_2, r.recommended_price_2)} |"
            )
        return "\n".join(lines)

    def generate_daily_report(
        self,
        daily_results: List[DailyChangeResult],
        labels: dict,
        prev_date: str,
        curr_date: str,
        threshold: float = 0.05,
    ) -> str:
        """
        前日比較レポートを生成する。
        5%以上の変動がある商品を「確認事項」として強調表示する。
        """
        p1 = labels["price1"]
        p2 = labels["price2"]

        changed = [r for r in daily_results if r.changed]
        significant = [r for r in daily_results if r.is_significant]

        if not changed:
            return (
                f"## 📊 前日比較（{prev_date} → {curr_date}）\n"
                "前日から変動のある商品はありませんでした。"
            )

        lines = [
            f"## 📊 前日比較（{prev_date} → {curr_date}）",
            f"変動: {len(changed)} 商品 / うち **5%以上: {len(significant)} 商品**",
            "",
        ]

        # 確認事項（5%以上変動）
        if significant:
            lines += [
                "### ⚠️ 確認事項（5%以上の価格変動）",
                "",
                f"| 商品名 | 型式 | 前日{p1} | 今日{p1} | 変動率 | 前日{p2} | 今日{p2} | 変動率 |",
                "|--------|------|----------|----------|--------|----------|----------|--------|",
            ]
            for r in significant:
                def pct_str(pct):
                    if pct is None:
                        return "—"
                    sign = "▲" if pct > 0 else "▼"
                    return f"**{sign}{abs(pct)*100:.1f}%**"

                lines.append(
                    f"| {r.name} | {r.code} "
                    f"| {_fmt(r.prev_price_1)} | {_fmt(r.curr_price_1)} | {pct_str(r.change_pct_1)} "
                    f"| {_fmt(r.prev_price_2)} | {_fmt(r.curr_price_2)} | {pct_str(r.change_pct_2)} |"
                )
            lines.append("")

        # 全変動リスト（5%未満含む）
        minor = [r for r in changed if not r.is_significant]
        if minor:
            lines += [
                "### 📋 その他の変動（5%未満）",
                "",
                f"| 商品名 | 型式 | 前日{p1} | 今日{p1} | 前日{p2} | 今日{p2} |",
                "|--------|------|----------|----------|----------|----------|",
            ]
            for r in minor:
                lines.append(
                    f"| {r.name} | {r.code} "
                    f"| {_fmt(r.prev_price_1)} | {_fmt(r.curr_price_1)} "
                    f"| {_fmt(r.prev_price_2)} | {_fmt(r.curr_price_2)} |"
                )

        return "\n".join(lines)
