"""BR-02 — Exact PAN string comparison (uppercase stripped)."""

from __future__ import annotations

from modules.verification.rules._text import normalize_pan
from modules.verification.schemas import RuleEvaluationContext, RuleEvaluationResult

RULE_CODE = "BR-02"
RULE_NAME = "pan_match"


def evaluate(context: RuleEvaluationContext) -> RuleEvaluationResult:
    declared = normalize_pan(context.snapshot.pan)
    provenance: list[dict[str, object]] = [
        {"layer": "snapshot", "field": "pan", "value": declared}
    ]
    pan_fields = [field for field in context.fields if field.field_name == "pan"]
    if not pan_fields:
        return RuleEvaluationResult(
            rule_code=RULE_CODE,
            rule_name=RULE_NAME,
            status="flagged",
            severity="high",
            message="No extracted PAN available to corroborate declared PAN.",
            diff_payload={"declared_pan": declared, "extracted_pans": []},
            provenance_sources=provenance,
        )

    extracted: list[dict[str, object]] = []
    mismatches: list[str] = []
    for field in pan_fields:
        text, layer = field.effective_text()
        if text is None:
            continue
        normalized = normalize_pan(text)
        extracted.append(
            {
                "pan": normalized,
                "layer": layer,
                "document_type": field.document_type,
            }
        )
        provenance.append(
            {
                "layer": layer,
                "field": "pan",
                "document_type": field.document_type,
                "value": normalized,
            }
        )
        if normalized != declared:
            mismatches.append(normalized)

    if not extracted:
        return RuleEvaluationResult(
            rule_code=RULE_CODE,
            rule_name=RULE_NAME,
            status="flagged",
            severity="high",
            message="PAN fields present but empty after layer resolution.",
            diff_payload={"declared_pan": declared, "extracted_pans": []},
            provenance_sources=provenance,
        )

    if mismatches:
        return RuleEvaluationResult(
            rule_code=RULE_CODE,
            rule_name=RULE_NAME,
            status="failed",
            severity="critical",
            message="Extracted PAN does not exactly match declared PAN.",
            diff_payload={
                "declared_pan": declared,
                "extracted_pans": extracted,
                "mismatches": mismatches,
            },
            provenance_sources=provenance,
        )

    return RuleEvaluationResult(
        rule_code=RULE_CODE,
        rule_name=RULE_NAME,
        status="passed",
        severity="info",
        message="Extracted PAN exactly matches declared PAN.",
        diff_payload={"declared_pan": declared, "extracted_pans": extracted},
        provenance_sources=provenance,
    )
