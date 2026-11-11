"""BR-05 — Bank salary credit vs salary-slip net pay (± tolerance)."""

from __future__ import annotations

from decimal import Decimal

from modules.verification.rules._text import parse_decimal, within_tolerance
from modules.verification.schemas import RuleEvaluationContext, RuleEvaluationResult

RULE_CODE = "BR-05"
RULE_NAME = "salary_credit_corroboration"


def evaluate(context: RuleEvaluationContext) -> RuleEvaluationResult:
    tolerance = context.tolerances.salary_credit_tolerance_pct
    net_pays = list(context.salary_slip_net_pays)
    if not net_pays:
        # Fall back to layered net_salary fields when structured slips absent.
        for field in context.fields:
            if field.field_name != "net_salary":
                continue
            text, _layer = field.effective_text()
            amount = parse_decimal(text)
            if amount is not None:
                net_pays.append(amount)

    credits = list(context.salary_credits)
    provenance: list[dict[str, object]] = []
    for amount in net_pays:
        provenance.append(
            {"layer": "normalized", "field": "net_salary", "value": str(amount)}
        )
    for credit in credits:
        provenance.append(
            {
                "layer": "normalized",
                "field": "identified_salary_credit",
                "value": str(credit.amount),
                "statement_month": credit.statement_month.isoformat(),
            }
        )

    if not net_pays:
        return RuleEvaluationResult(
            rule_code=RULE_CODE,
            rule_name=RULE_NAME,
            status="flagged",
            severity="medium",
            message="No salary-slip net pay available for bank credit corroboration.",
            diff_payload={"net_pays": [], "salary_credits": []},
            provenance_sources=provenance,
        )
    if not credits:
        return RuleEvaluationResult(
            rule_code=RULE_CODE,
            rule_name=RULE_NAME,
            status="flagged",
            severity="medium",
            message="No identified bank salary credits available for corroboration.",
            diff_payload={
                "net_pays": [str(v) for v in net_pays],
                "salary_credits": [],
            },
            provenance_sources=provenance,
        )

    reference_net = max(net_pays)
    matched = False
    comparisons: list[dict[str, object]] = []
    for credit in credits:
        ok = within_tolerance(
            credit.amount,
            reference_net,
            tolerance_pct=tolerance,
        )
        comparisons.append(
            {
                "credit_amount": str(credit.amount),
                "net_pay": str(reference_net),
                "within_tolerance": ok,
                "statement_month": credit.statement_month.isoformat(),
            }
        )
        if ok:
            matched = True

    if matched:
        return RuleEvaluationResult(
            rule_code=RULE_CODE,
            rule_name=RULE_NAME,
            status="passed",
            severity="info",
            message=(
                f"At least one salary credit is within ±{tolerance}% "
                f"of salary-slip net pay."
            ),
            diff_payload={
                "reference_net_pay": str(reference_net),
                "tolerance_pct": str(tolerance),
                "comparisons": comparisons,
            },
            provenance_sources=provenance,
        )

    return RuleEvaluationResult(
        rule_code=RULE_CODE,
        rule_name=RULE_NAME,
        status="failed",
        severity="high",
        message=(
            f"No salary credit within ±{tolerance}% of salary-slip net pay "
            f"({reference_net})."
        ),
        diff_payload={
            "reference_net_pay": str(reference_net),
            "tolerance_pct": str(tolerance),
            "comparisons": comparisons,
        },
        provenance_sources=provenance,
    )
