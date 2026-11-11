"""BR-08 — Verified/declared income vs policy min_monthly_income."""

from __future__ import annotations

from decimal import Decimal

from modules.verification.rules._text import parse_decimal
from modules.verification.schemas import RuleEvaluationContext, RuleEvaluationResult

RULE_CODE = "BR-08"
RULE_NAME = "min_monthly_income"


def evaluate(context: RuleEvaluationContext) -> RuleEvaluationResult:
    minimum = context.policy_thresholds.min_monthly_income
    candidates: list[Decimal] = []
    provenance: list[dict[str, object]] = [
        {
            "layer": "policy",
            "field": "min_monthly_income",
            "value": str(minimum),
        }
    ]

    for amount in context.salary_slip_net_pays:
        candidates.append(amount)
        provenance.append(
            {"layer": "normalized", "field": "net_salary", "value": str(amount)}
        )
    for field in context.fields:
        if field.field_name != "net_salary":
            continue
        text, layer = field.effective_text()
        amount = parse_decimal(text)
        if amount is None:
            continue
        candidates.append(amount)
        provenance.append(
            {
                "layer": layer,
                "field": "net_salary",
                "document_type": field.document_type,
                "value": str(amount),
            }
        )

    if candidates:
        income = max(candidates)
        income_source = "extracted_net_salary"
    else:
        income = context.snapshot.declared_net_monthly_income
        income_source = "declared_net_monthly_income"
        provenance.append(
            {
                "layer": "snapshot",
                "field": "declared_net_monthly_income",
                "value": str(income),
            }
        )

    payload = {
        "income": str(income),
        "income_source": income_source,
        "min_monthly_income": str(minimum),
    }
    if income >= minimum:
        return RuleEvaluationResult(
            rule_code=RULE_CODE,
            rule_name=RULE_NAME,
            status="passed",
            severity="info",
            message=(
                f"Income {income} meets policy minimum monthly income "
                f"of {minimum}."
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
            f"Income {income} is below policy minimum monthly income "
            f"of {minimum}."
        ),
        diff_payload=payload,
        provenance_sources=provenance,
    )
