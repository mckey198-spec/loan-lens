"""BR-11 — Mandatory document requirements checklist."""

from __future__ import annotations

from collections import Counter

from modules.verification.schemas import RuleEvaluationContext, RuleEvaluationResult

RULE_CODE = "BR-11"
RULE_NAME = "document_completeness"


def evaluate(context: RuleEvaluationContext) -> RuleEvaluationResult:
    inventory_types: list[str] = []
    for item in context.snapshot.document_inventory:
        doc_type = item.get("document_type") or item.get("type")
        if doc_type:
            inventory_types.append(str(doc_type))

    counts = Counter(inventory_types)
    provenance: list[dict[str, object]] = [
        {
            "layer": "snapshot",
            "field": "document_inventory",
            "document_types": sorted(counts.items()),
        },
        {
            "layer": "policy",
            "field": "mandatory_documents",
            "requirements": [
                {
                    "document_type": req.document_type,
                    "min_count": req.min_count,
                    "is_mandatory": req.is_mandatory,
                }
                for req in context.mandatory_documents
            ],
        },
    ]

    mandatory = [
        req for req in context.mandatory_documents if req.is_mandatory
    ]
    if not mandatory:
        return RuleEvaluationResult(
            rule_code=RULE_CODE,
            rule_name=RULE_NAME,
            status="passed",
            severity="info",
            message="No mandatory document requirements configured for applicant type.",
            diff_payload={
                "inventory_counts": dict(counts),
                "missing": [],
            },
            provenance_sources=provenance,
        )

    missing: list[dict[str, object]] = []
    for req in mandatory:
        present = counts.get(req.document_type, 0)
        needed = max(1, req.min_count)
        if present < needed:
            missing.append(
                {
                    "document_type": req.document_type,
                    "required_count": needed,
                    "present_count": present,
                }
            )

    payload = {
        "inventory_counts": dict(counts),
        "missing": missing,
    }
    if missing:
        missing_types = ", ".join(item["document_type"] for item in missing)
        return RuleEvaluationResult(
            rule_code=RULE_CODE,
            rule_name=RULE_NAME,
            status="failed",
            severity="critical",
            message=f"Missing mandatory document(s): {missing_types}.",
            diff_payload=payload,
            provenance_sources=provenance,
        )

    return RuleEvaluationResult(
        rule_code=RULE_CODE,
        rule_name=RULE_NAME,
        status="passed",
        severity="info",
        message="All mandatory documents are present in the inventory.",
        diff_payload=payload,
        provenance_sources=provenance,
    )
