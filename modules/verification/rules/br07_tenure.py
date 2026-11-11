"""BR-07 — Employment tenure vs policy min_employment_months."""

from __future__ import annotations

from modules.verification.schemas import RuleEvaluationContext, RuleEvaluationResult

RULE_CODE = "BR-07"
RULE_NAME = "employment_tenure"


def evaluate(context: RuleEvaluationContext) -> RuleEvaluationResult:
    tenure = context.snapshot.time_in_job_or_business_months
    minimum = context.policy_thresholds.min_employment_months
    provenance = [
        {
            "layer": "snapshot",
            "field": "time_in_job_or_business_months",
            "value": tenure,
        },
        {
            "layer": "policy",
            "field": "min_employment_months",
            "value": minimum,
        },
    ]
    payload = {
        "time_in_job_or_business_months": tenure,
        "min_employment_months": minimum,
    }
    if tenure >= minimum:
        return RuleEvaluationResult(
            rule_code=RULE_CODE,
            rule_name=RULE_NAME,
            status="passed",
            severity="info",
            message=(
                f"Employment tenure {tenure} months meets policy minimum "
                f"of {minimum} months."
            ),
            diff_payload=payload,
            provenance_sources=provenance,
        )

    return RuleEvaluationResult(
        rule_code=RULE_CODE,
        rule_name=RULE_NAME,
        status="failed",
        severity="medium",
        message=(
            f"Employment tenure {tenure} months is below policy minimum "
            f"of {minimum} months."
        ),
        diff_payload=payload,
        provenance_sources=provenance,
    )
