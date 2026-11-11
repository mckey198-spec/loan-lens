"""BR-01 — Name match across declared and document sources."""

from __future__ import annotations

from decimal import Decimal

from modules.verification.rules._text import token_jaccard
from modules.verification.schemas import RuleEvaluationContext, RuleEvaluationResult

RULE_CODE = "BR-01"
RULE_NAME = "name_match"


def evaluate(context: RuleEvaluationContext) -> RuleEvaluationResult:
    declared = context.snapshot.applicant_full_name
    name_fields = [
        field for field in context.fields if field.field_name == "applicant_name"
    ]
    provenance: list[dict[str, object]] = [
        {"layer": "snapshot", "field": "applicant_full_name", "value": declared}
    ]
    if not name_fields:
        return RuleEvaluationResult(
            rule_code=RULE_CODE,
            rule_name=RULE_NAME,
            status="flagged",
            severity="medium",
            message="No extracted applicant name available to corroborate declared name.",
            diff_payload={"declared_name": declared, "extracted_names": []},
            provenance_sources=provenance,
        )

    threshold = context.tolerances.name_match_min_ratio
    scores: list[dict[str, object]] = []
    worst: Decimal | None = None
    for field in name_fields:
        text, layer = field.effective_text()
        if text is None:
            continue
        score = token_jaccard(declared, text)
        scores.append(
            {
                "extracted_name": text,
                "layer": layer,
                "document_type": field.document_type,
                "similarity": str(score),
            }
        )
        provenance.append(
            {
                "layer": layer,
                "field": "applicant_name",
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
            message="Applicant name fields present but empty after layer resolution.",
            diff_payload={"declared_name": declared, "extracted_names": []},
            provenance_sources=provenance,
        )

    if worst >= threshold:
        return RuleEvaluationResult(
            rule_code=RULE_CODE,
            rule_name=RULE_NAME,
            status="passed",
            severity="info",
            message=(
                f"Name tokens match across sources "
                f"(min similarity {worst} >= {threshold})."
            ),
            diff_payload={
                "declared_name": declared,
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
        severity="high",
        message=(
            f"Name mismatch across sources "
            f"(min similarity {worst} < {threshold})."
        ),
        diff_payload={
            "declared_name": declared,
            "comparisons": scores,
            "min_similarity": str(worst),
            "threshold": str(threshold),
        },
        provenance_sources=provenance,
    )
