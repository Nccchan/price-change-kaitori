"""買取価格の大幅値上げを承認待ちにする純粋ロジック。"""
from dataclasses import dataclass
from typing import List, Optional, Tuple


# 2026-08-14 なつき決定: 値上げ方向は自動で通す（A案）＋上限を引き上げる（5%→20%）。
# 2026-09-17 なつき決定（T-131 → A・09:11）: 値上げも幅を問わず自動反映する。
# 5%超は通常の競合追随幅を毎日ブロックし、承認導線が無いため永久に解消しなかった＝
# 安すぎる買取が公開され続けていた。20%でも同じ理屈で日常の値上げ幅を止めてしまうため撤廃し、
# 値下げガード（price_decrease_guard）と対称に「前回比+50%以上＝取得ミス疑い」だけを保留する。
# 逆ざや（買取＞販売・競合最安）は price_guard 側の別ガードでこれとは独立に効く。
INCREASE_RATE_LIMIT = 0.50


@dataclass(frozen=True)
class IncreaseHold:
    name: str
    code: str
    unit: str
    current: int
    proposed: int
    increase_yen: int
    increase_rate: float


def should_hold(current: Optional[int], proposed: Optional[int]) -> Tuple[bool, int, float]:
    if current is None or proposed is None or proposed <= current:
        return False, 0, 0.0
    increase = proposed - current
    rate = increase / current if current > 0 else 1.0
    return rate > INCREASE_RATE_LIMIT, increase, rate


def evaluate_result(game: str, result) -> List[IncreaseHold]:
    units = [("BOX", result.current_price_1, result.recommended_price_1)]
    unit2 = "NS" if game == "pokemon" else "CARTON"
    units.append((unit2, result.current_price_2, result.recommended_price_2))
    holds = []
    for unit, current, proposed in units:
        hold, increase, rate = should_hold(current, proposed)
        if hold:
            holds.append(IncreaseHold(
                name=result.name, code=result.code, unit=unit,
                current=int(current), proposed=int(proposed),
                increase_yen=increase, increase_rate=rate,
            ))
    return holds


def format_holds(holds: List[IncreaseHold]) -> str:
    if not holds:
        return ""
    lines = [f"⚠️ 買取価格 50%以上の急騰（取得ミス疑い・要確認）: {len(holds)}件"]
    for hold in holds:
        lines.append(
            f"- {hold.name} ({hold.code or '型番なし'}) {hold.unit}: "
            f"¥{hold.current:,}→¥{hold.proposed:,} "
            f"(+¥{hold.increase_yen:,}, +{hold.increase_rate:.1%})"
        )
    lines.append("反映する場合は承認済みproposalを指定してください。")
    return "\n".join(lines)
