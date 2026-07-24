"""自社輸出売値を基準にした買取粗利上限。外部相場は使用しない。"""
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List


@dataclass(frozen=True)
class GrossProfitParameters:
    payment_fee_rate: float
    sales_fee_rate: float
    duty_rate: float
    target_gross_margin_rate: float

    def validate(self) -> None:
        values = (
            self.payment_fee_rate, self.sales_fee_rate,
            self.duty_rate, self.target_gross_margin_rate,
        )
        if any(value < 0 or value >= 1 for value in values):
            raise ValueError("粗利パラメータは0以上1未満で設定してください")
        if sum(values) >= 1:
            raise ValueError("率の合計が100%以上です")


def max_buyback_jpy(export_sale_jpy: int, shipping_jpy: int, params: GrossProfitParameters) -> int:
    params.validate()
    if export_sale_jpy <= 0 or shipping_jpy < 0:
        raise ValueError("輸出売値と送料が未設定または不正です")
    rate_cost = export_sale_jpy * (
        params.payment_fee_rate + params.sales_fee_rate
        + params.duty_rate + params.target_gross_margin_rate
    )
    return int(export_sale_jpy - shipping_jpy - rate_cost)


def validate_proposal_gross_margin(
    proposal: Dict[str, Any], params: GrossProfitParameters,
) -> List[Dict[str, Any]]:
    violations = []
    for item in proposal.get("items", []):
        if item.get("export_sale_jpy") is None or item.get("shipping_jpy") is None:
            raise ValueError(f"{item.get('code')}/{item.get('unit')}: 粗利上限パラメータ未設定")
        ceiling = max_buyback_jpy(int(item["export_sale_jpy"]), int(item["shipping_jpy"]), params)
        if int(item["proposed"]) > ceiling:
            violations.append({
                "code": item["code"], "unit": item["unit"],
                "proposed": int(item["proposed"]), "ceiling": ceiling,
            })
    return violations
