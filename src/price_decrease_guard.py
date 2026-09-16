"""買取価格の値下げガード（純粋ロジック）。

2026-09-16 なつき決定（Telegram 15:38）「値下げは全部承認」により、
値下げは幅を問わず自動反映する。保留するのは取得ミス疑いの
50%以上の急落のみ（例: 30th CELEBRATION FUTURISTIC BOX 50,000→35,000 = -30%は自動反映対象、
50%を超えるような値は取得ミスを疑って要確認）。
"""
from dataclasses import dataclass
from typing import List, Optional, Tuple


DECREASE_RATE_LIMIT = 0.50
# 以下2定数は 2026-09-16 なつき決定「値下げは全承認」により判定に未使用。
# 円額での保留はしない（should_hold は decrease_rate のみで判定する）。互換のため残置。
BOX_NS_YEN_LIMIT = 3_000
CARTON_YEN_LIMIT = 20_000


@dataclass(frozen=True)
class DecreaseHold:
    name: str
    code: str
    unit: str
    current: int
    proposed: int
    decrease_yen: int
    decrease_rate: float


def should_hold(current: Optional[int], proposed: Optional[int], unit: str) -> Tuple[bool, int, float]:
    if current is None or proposed is None or proposed >= current:
        return False, 0, 0.0
    decrease = current - proposed
    rate = decrease / current if current > 0 else 0.0
    return rate >= DECREASE_RATE_LIMIT, decrease, rate


def evaluate_result(game: str, result) -> List[DecreaseHold]:
    units = [("BOX", result.current_price_1, result.recommended_price_1)]
    unit2 = "NS" if game == "pokemon" else "CARTON"
    units.append((unit2, result.current_price_2, result.recommended_price_2))
    holds = []
    for unit, current, proposed in units:
        hold, decrease, rate = should_hold(current, proposed, unit)
        if hold:
            holds.append(DecreaseHold(
                name=result.name, code=result.code, unit=unit,
                current=int(current), proposed=int(proposed),
                decrease_yen=decrease, decrease_rate=rate,
            ))
    return holds


def format_holds(holds: List[DecreaseHold]) -> str:
    if not holds:
        return ""
    lines = [f"⚠️ 買取価格 50%以上の急落（取得ミス疑い・要確認）: {len(holds)}件"]
    for h in holds:
        lines.append(
            f"- {h.name} ({h.code or '型番なし'}) {h.unit}: "
            f"¥{h.current:,}→¥{h.proposed:,} "
            f"(-¥{h.decrease_yen:,}, -{h.decrease_rate:.1%})"
        )
    lines.append("反映する場合は「反映して」と指示してください。")
    return "\n".join(lines)
