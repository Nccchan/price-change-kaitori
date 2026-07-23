"""買取価格の書込前ガード。外部I/Oを持たない純粋ロジック。"""
from dataclasses import dataclass
from typing import Iterable, List, Tuple


BATCH_STOP_COUNT = 3
BATCH_STOP_RATE = 0.05


@dataclass(frozen=True)
class PriceGuardViolation:
    name: str
    code: str
    box: int
    ns: int
    reason: str


class BatchPriceGuardError(ValueError):
    def __init__(self, violations: List[PriceGuardViolation], candidates: int):
        self.violations = violations
        self.candidates = candidates
        rate = len(violations) / candidates if candidates else 0.0
        super().__init__(
            f"NS<=BOX guard: batch stopped: {len(violations)}/{candidates} "
            f"({rate:.1%}) invalid"
        )


def validate_pokemon_payloads(payloads: Iterable) -> Tuple[list, List[PriceGuardViolation]]:
    """異常商品を除外。3件以上または候補の5%以上ならバッチ全体を停止する。"""
    payloads = list(payloads)
    candidates = 0
    violations: List[PriceGuardViolation] = []
    rejected_ids = set()
    for payload in payloads:
        box = getattr(payload, "new_price_1", None)
        ns = getattr(payload, "new_price_2", None)
        if box is None or ns is None:
            continue
        candidates += 1
        if ns <= 0 or box <= 0 or ns > box:
            reason = "non-positive price" if ns <= 0 or box <= 0 else "NS exceeds BOX"
            violations.append(PriceGuardViolation(
                name=getattr(payload, "name", ""), code=getattr(payload, "code", ""),
                box=int(box), ns=int(ns), reason=reason,
            ))
            rejected_ids.add(id(payload))

    rate = len(violations) / candidates if candidates else 0.0
    if violations and (len(violations) >= BATCH_STOP_COUNT or rate >= BATCH_STOP_RATE):
        raise BatchPriceGuardError(violations, candidates)
    return [p for p in payloads if id(p) not in rejected_ids], violations


def guard_payloads(game: str, payloads: Iterable) -> Tuple[list, List[PriceGuardViolation]]:
    payloads = list(payloads)
    if game != "pokemon":
        return payloads, []
    return validate_pokemon_payloads(payloads)
