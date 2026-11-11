"""Classification result schemas (Architecture §7 document intelligence)."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

DocumentType = Literal[
    "identity_proof",
    "pan_card",
    "address_proof",
    "salary_slip",
    "bank_statement",
    "form_16",
    "itr",
    "gst_return",
    "business_proof",
    "existing_loan_statement",
    "unsupported",
    "other",
]

ClassificationMethod = Literal["heuristic_rules", "model_fallback"]


class ClassificationResult(BaseModel):
    """Outcome of classifying one document's text (or empty/unreadable bytes)."""

    model_config = ConfigDict(from_attributes=True)

    document_type: DocumentType
    confidence: Decimal = Field(ge=Decimal("0.0"), le=Decimal("1.0"))
    method: ClassificationMethod
    evidence_snippets: list[str] = Field(default_factory=list)

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_confidence(cls, value: object) -> Decimal:
        if isinstance(value, Decimal):
            return value
        return Decimal(str(value))
