"""BR-04 — Employer name corroboration across declared and salary-slip sources."""

from __future__ import annotations

from decimal import Decimal

from modules.verification.rules._text import token_jaccard
from modules.verification.schemas import RuleEvaluationContext, RuleEvaluationResult

RULE_CODE = "BR-04"
RULE_NAME = "employer_match"


def evaluate(context: RuleEvaluationContext) -> RuleEvaluationResult:
    declared = context.snapshot.employer_or_business_name
    threshold = context.tolerances.employer_match_min_ratio
    provenance: list[dict[str, object]] = [
        {
            "layer": "snapshot",
            "field": "employer_or_business_name",
            "value": declared,
        }
    ]
    employer_fields = [
        field for field in context.fields if field.field_name == "employer_name"
    ]
    if not employer_fields:
        return RuleEvaluationResult(
            rule_code=RULE_CODE,
            rule_name=RULE_NAME,
            status="flagged",
            severity="medium",
            message="No extracted employer name available to corroborate declared employer.",
            diff_payload={"declared_employer": declared, "extracted_employers": []},
            provenance_sources=provenance,
        )

    scores: list[dict[str, object]] = []
    worst: Decimal | None = None
    for field in employer_fields:
        text, layer = field.effective_text()
        if text is None:
            continue
        score = token_jaccard(declared, text)
        scores.append(
            {
                "extracted_employer": text,
                "layer": layer,
                "document_type": field.document_type,
                "similarity": str(score),
            }
        )
        provenance.append(
            {
                "layer": layer,
                "field": "employer_name",
                "document_type": field.document_type,
                "value": text,
            }
        )
        if worst is None or score < worst:
            worst = score

    if not scores or worst is None:
        return RuleEvaluationResult(
            rule_code=RULE_CODE,
            rule_name=RULE_NAME,
            status="flagged",
            severity="medium",
            message="Employer fields present but empty after layer resolution.",
            diff_payload={"declared_employer": declared, "extracted_employers": []},
            provenance_sources=provenance,
        )

    if worst >= threshold:
        return RuleEvaluationResult(
            rule_code=RULE_CODE,
            rule_name=RULE_NAME,
            status="passed",
            severity="info",
            message=(
                f"Employer name corroborated "
                f"(min similarity {worst} >= {threshold})."
            ),
            diff_payload={
                "declared_employer": declared,
                "comparisons": scores,
                "min_similarity": str(worst),
                "threshold": str(threshold),
            },
            provenance_sources=provenance,
        )

    return RuleEvaluationResult(
        rule_code=RULE_CODE,
        rule_name=RULE_NAME,
        status="failed",
        severity="medium",
        message=(
            f"Employer name not corroborated "
            f"(min similarity {worst} < {threshold})."
        ),
        diff_payload={
            "declared_employer": declared,
            "comparisons": scores,
            "min_similarity": str(worst),
            "threshold": str(threshold),
        },
        provenance_sources=provenance,
    )
