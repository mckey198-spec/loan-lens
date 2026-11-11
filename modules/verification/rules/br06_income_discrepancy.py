"""BR-06 — Declared income vs extracted slip income (± tolerance)."""

from __future__ import annotations

from decimal import Decimal

from modules.verification.rules._text import parse_decimal, within_tolerance
from modules.verification.schemas import RuleEvaluationContext, RuleEvaluationResult

RULE_CODE = "BR-06"
RULE_NAME = "income_discrepancy"


def evaluate(context: RuleEvaluationContext) -> RuleEvaluationResult:
    declared = context.snapshot.declared_net_monthly_income
    tolerance = context.tolerances.income_discrepancy_tolerance_pct
    provenance: list[dict[str, object]] = [
        {
            "layer": "snapshot",
            "field": "declared_net_monthly_income",
            "value": str(declared),
        }
    ]

    slip_incomes: list[Decimal] = list(context.salary_slip_net_pays)
    for field in context.fields:
        if field.field_name != "net_salary":
            continue
        text, layer = field.effective_text()
        amount = parse_decimal(text)
        if amount is None:
            continue
        slip_incomes.append(amount)
        provenance.append(
            {
                "layer": layer,
                "field": "net_salary",
                "document_type": field.document_type,
                "value": str(amount),
            }
        )

    if not slip_incomes:
        return RuleEvaluationResult(
            rule_code=RULE_CODE,
            rule_name=RULE_NAME,
            status="flagged",
            severity="medium",
            message="No extracted slip income available to compare with declared income.",
            diff_payload={
                "declared_net_monthly_income": str(declared),
                "extracted_incomes": [],
            },
            provenance_sources=provenance,
        )

    # Prefer the maximum observed slip net pay as the verification candidate.
    extracted = max(slip_incomes)
    ok = within_tolerance(declared, extracted, tolerance_pct=tolerance)
    payload = {
        "declared_net_monthly_income": str(declared),
        "extracted_net_income": str(extracted),
        "tolerance_pct": str(tolerance),
        "all_extracted": [str(v) for v in slip_incomes],
    }
    if ok:
        return RuleEvaluationResult(
            rule_code=RULE_CODE,
            rule_name=RULE_NAME,
            status="passed",
            severity="info",
            message=(
                f"Declared net income agrees with slip income "
                f"within ±{tolerance}%."
            ),
            diff_payload=payload,
            provenance_sources=provenance,
        )

    return RuleEvaluationResult(
        rule_code=RULE_CODE,
        rule_name=RULE_NAME,
        status="failed",
        severity="high",
        message=(
            f"Declared net income differs from slip income "
            f"beyond ±{tolerance}%."
        ),
        diff_payload=payload,
        provenance_sources=provenance,
    )
