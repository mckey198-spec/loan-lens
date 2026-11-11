"""BR-10 — Declared obligations vs recurring debit patterns."""

from __future__ import annotations

from decimal import Decimal

from modules.verification.rules._text import within_tolerance
from modules.verification.schemas import RuleEvaluationContext, RuleEvaluationResult

RULE_CODE = "BR-10"
RULE_NAME = "emi_corroboration"

# Recurring-debit vs declared EMI uses the same absolute-percent band as income
# discrepancy unless a dedicated tolerance is later added to policy config.
_DEFAULT_EMI_TOLERANCE_PCT = Decimal("10")


def evaluate(context: RuleEvaluationContext) -> RuleEvaluationResult:
    declared = context.snapshot.declared_existing_emis
    provenance: list[dict[str, object]] = [
        {
            "layer": "snapshot",
            "field": "declared_existing_emis",
            "value": str(declared),
        }
    ]
    debits = list(context.recurring_debits)
    for debit in debits:
        provenance.append(
            {
                "layer": "normalized",
                "field": "recurring_debit",
                "label": debit.label,
                "value": str(debit.monthly_amount),
                "occurrence_count": debit.occurrence_count,
            }
        )

    observed_total = sum(
        (debit.monthly_amount for debit in debits),
        start=Decimal("0.00"),
    )
    payload = {
        "declared_existing_emis": str(declared),
        "observed_recurring_debits_total": str(observed_total),
        "recurring_debits": [
            {
                "label": debit.label,
                "monthly_amount": str(debit.monthly_amount),
                "occurrence_count": debit.occurrence_count,
            }
            for debit in debits
        ],
        "tolerance_pct": str(_DEFAULT_EMI_TOLERANCE_PCT),
    }

    if declared == 0 and not debits:
        return RuleEvaluationResult(
            rule_code=RULE_CODE,
            rule_name=RULE_NAME,
            status="passed",
            severity="info",
            message="No declared EMIs and no recurring debit patterns observed.",
            diff_payload=payload,
            provenance_sources=provenance,
        )

    if declared == 0 and observed_total > 0:
        return RuleEvaluationResult(
            rule_code=RULE_CODE,
            rule_name=RULE_NAME,
            status="failed",
            severity="high",
            message=(
                "Recurring debit patterns suggest undeclared obligations "
                f"(observed {observed_total})."
            ),
            diff_payload=payload,
            provenance_sources=provenance,
        )

    if declared > 0 and not debits:
        return RuleEvaluationResult(
            rule_code=RULE_CODE,
            rule_name=RULE_NAME,
            status="flagged",
            severity="medium",
            message=(
                "Declared EMIs present but no recurring debit patterns were identified."
            ),
            diff_payload=payload,
            provenance_sources=provenance,
        )

    if within_tolerance(
        declared,
        observed_total,
        tolerance_pct=_DEFAULT_EMI_TOLERANCE_PCT,
    ):
        return RuleEvaluationResult(
            rule_code=RULE_CODE,
            rule_name=RULE_NAME,
            status="passed",
            severity="info",
            message=(
                "Recurring debit patterns corroborate declared EMIs "
                f"within ±{_DEFAULT_EMI_TOLERANCE_PCT}%."
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
            "Recurring debit total disagrees with declared EMIs "
            f"beyond ±{_DEFAULT_EMI_TOLERANCE_PCT}%."
        ),
        diff_payload=payload,
        provenance_sources=provenance,
    )
