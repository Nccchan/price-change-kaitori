"""買取価格の大幅値下げを承認待ちにする純粋ロジック。"""
from dataclasses import dataclass
from typing import List, Optional, Tuple


DECREASE_RATE_LIMIT = 0.10
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
    yen_limit = CARTON_YEN_LIMIT if unit == "CARTON" else BOX_NS_YEN_LIMIT
    return rate > DECREASE_RATE_LIMIT or decrease > yen_limit, decrease, rate


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
    lines = [f"⚠️ 買取価格 大幅値下げ要承認: {len(holds)}件"]
    for h in holds:
        lines.append(
            f"- {h.name} ({h.code or '型番なし'}) {h.unit}: "
            f"¥{h.current:,}→¥{h.proposed:,} "
            f"(-¥{h.decrease_yen:,}, -{h.decrease_rate:.1%})"
        )
    lines.append("反映する場合は「反映して」と指示してください。")
    return "\n".join(lines)
