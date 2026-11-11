"""BR-03 — Exact date of birth comparison."""

from __future__ import annotations

from datetime import date

from modules.verification.schemas import RuleEvaluationContext, RuleEvaluationResult

RULE_CODE = "BR-03"
RULE_NAME = "dob_match"


def _as_iso_date(value: object) -> str | None:
    if isinstance(value, date):
        return value.isoformat()
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def evaluate(context: RuleEvaluationContext) -> RuleEvaluationResult:
    declared = context.snapshot.date_of_birth.isoformat()
    provenance: list[dict[str, object]] = [
        {"layer": "snapshot", "field": "date_of_birth", "value": declared}
    ]
    dob_fields = [
        field for field in context.fields if field.field_name == "date_of_birth"
    ]
    if not dob_fields:
        return RuleEvaluationResult(
            rule_code=RULE_CODE,
            rule_name=RULE_NAME,
            status="flagged",
            severity="high",
            message="No extracted date of birth available to corroborate declared DOB.",
            diff_payload={"declared_dob": declared, "extracted_dobs": []},
            provenance_sources=provenance,
        )

    extracted: list[dict[str, object]] = []
    mismatches: list[str] = []
    for field in dob_fields:
        text, layer = field.effective_text()
        iso = _as_iso_date(text)
        if iso is None:
            continue
        extracted.append(
            {
                "dob": iso,
                "layer": layer,
                "document_type": field.document_type,
            }
        )
        provenance.append(
            {
                "layer": layer,
                "field": "date_of_birth",
                "document_type": field.document_type,
                "value": iso,
            }
        )
        if iso != declared:
            mismatches.append(iso)

    if not extracted:
        return RuleEvaluationResult(
            rule_code=RULE_CODE,
            rule_name=RULE_NAME,
            status="flagged",
            severity="high",
            message="Date of birth fields present but empty after layer resolution.",
            diff_payload={"declared_dob": declared, "extracted_dobs": []},
            provenance_sources=provenance,
        )

    if mismatches:
        return RuleEvaluationResult(
            rule_code=RULE_CODE,
            rule_name=RULE_NAME,
            status="failed",
            severity="critical",
            message="Extracted date of birth does not exactly match declared DOB.",
            diff_payload={
                "declared_dob": declared,
                "extracted_dobs": extracted,
                "mismatches": mismatches,
            },
            provenance_sources=provenance,
        )

    return RuleEvaluationResult(
        rule_code=RULE_CODE,
        rule_name=RULE_NAME,
        status="passed",
        severity="info",
        message="Extracted date of birth exactly matches declared DOB.",
        diff_payload={"declared_dob": declared, "extracted_dobs": extracted},
        provenance_sources=provenance,
    )
