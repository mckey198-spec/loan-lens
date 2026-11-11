"""BR-09 — Bank statement date-range continuity check."""

from __future__ import annotations

from datetime import timedelta

from modules.verification.schemas import RuleEvaluationContext, RuleEvaluationResult

RULE_CODE = "BR-09"
RULE_NAME = "statement_continuity"


def evaluate(context: RuleEvaluationContext) -> RuleEvaluationResult:
    periods = sorted(
        context.statement_periods,
        key=lambda item: (item.start_date, item.end_date),
    )
    provenance: list[dict[str, object]] = [
        {
            "layer": "normalized",
            "field": "statement_period",
            "start_date": period.start_date.isoformat(),
            "end_date": period.end_date.isoformat(),
            "document_id": (
                str(period.source_document_id)
                if period.source_document_id is not None
                else None
            ),
        }
        for period in periods
    ]

    if not periods:
        return RuleEvaluationResult(
            rule_code=RULE_CODE,
            rule_name=RULE_NAME,
            status="flagged",
            severity="medium",
            message="No bank statement periods available for continuity check.",
            diff_payload={"periods": [], "gaps": []},
            provenance_sources=provenance,
        )

    gaps: list[dict[str, object]] = []
    for previous, current in zip(periods, periods[1:]):
        expected_next = previous.end_date + timedelta(days=1)
        if current.start_date > expected_next:
            gaps.append(
                {
                    "after": previous.end_date.isoformat(),
                    "before": current.start_date.isoformat(),
                    "gap_days": (current.start_date - expected_next).days,
                }
            )

    period_payload = [
        {
            "start_date": period.start_date.isoformat(),
            "end_date": period.end_date.isoformat(),
        }
        for period in periods
    ]
    if gaps:
        return RuleEvaluationResult(
            rule_code=RULE_CODE,
            rule_name=RULE_NAME,
            status="failed",
            severity="medium",
            message=f"Bank statement coverage has {len(gaps)} gap(s).",
            diff_payload={"periods": period_payload, "gaps": gaps},
            provenance_sources=provenance,
        )

    return RuleEvaluationResult(
        rule_code=RULE_CODE,
        rule_name=RULE_NAME,
        status="passed",
        severity="info",
        message="Bank statement periods are continuous with no gaps.",
        diff_payload={"periods": period_payload, "gaps": []},
        provenance_sources=provenance,
    )
