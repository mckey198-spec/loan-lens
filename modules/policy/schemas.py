"""Policy document, version, threshold, requirement, and chunk schemas (M06-T02)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ApplicantType = Literal["salaried", "self_employed"]
PolicyStatus = Literal["draft", "active", "archived"]
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


class PolicyThresholdConfig(BaseModel):
    """Versioned numeric thresholds for one applicant type (BR-* inputs)."""

    model_config = ConfigDict(from_attributes=True)

    applicant_type: ApplicantType
    min_monthly_income: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    max_foir_pct: Decimal = Field(gt=0, le=100, max_digits=5, decimal_places=2)
    min_employment_months: int = Field(ge=0)
    max_loan_amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)


class PolicyThresholdConfigResponse(PolicyThresholdConfig):
    id: UUID
    policy_version_id: UUID
    created_at: datetime


class PolicyDocumentRequirement(BaseModel):
    """Mandatory/optional document checklist entry for one applicant type."""

    model_config = ConfigDict(from_attributes=True)

    applicant_type: ApplicantType
    document_type: DocumentType
    is_mandatory: bool = True


class PolicyDocumentRequirementResponse(PolicyDocumentRequirement):
    id: UUID
    policy_version_id: UUID
    created_at: datetime


class PolicyChunk(BaseModel):
    """Lexical chunk stored for PostgreSQL full-text search (no embeddings)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    policy_version_id: UUID
    chunk_index: int
    section_label: str
    content_text: str
    created_at: datetime


class PolicyChunkMatch(BaseModel):
    """One lexical full-text hit within a bound policy version (ADR-020)."""

    chunk_id: UUID
    section_label: str
    content_text: str
    rank_score: float


class PolicyCitationsResponse(BaseModel):
    """Retrieved citations scoped to an application's frozen policy version."""

    application_id: UUID
    policy_version_id: UUID
    citations: dict[str, list[PolicyChunkMatch]]


class PolicyChunkDraft(BaseModel):
    """In-memory chunk produced by the markdown heading chunker."""

    section_label: str = Field(min_length=1, max_length=512)
    content_text: str


class CreatePolicyDocumentRequest(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    lender_label: str = Field(default="synthetic_demo", min_length=1, max_length=256)

    @field_validator("title", "lender_label")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class PolicyDocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    lender_label: str
    created_by_user_id: UUID
    created_at: datetime


class CreatePolicyVersionRequest(BaseModel):
    version_label: str = Field(min_length=1, max_length=128)
    annual_interest_rate_pct: Decimal = Field(gt=0, le=100, max_digits=5, decimal_places=2)
    effective_from: date
    effective_to: date | None = None
    thresholds: list[PolicyThresholdConfig] = Field(min_length=1)
    doc_requirements: list[PolicyDocumentRequirement] = Field(default_factory=list)

    @field_validator("version_label")
    @classmethod
    def strip_version_label(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("version_label must not be blank")
        return stripped

    @model_validator(mode="after")
    def validate_date_range(self) -> CreatePolicyVersionRequest:
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to must be >= effective_from when set")
        return self


class PolicyVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    policy_document_id: UUID
    version_label: str
    status: PolicyStatus
    annual_interest_rate_pct: Decimal
    effective_from: date
    effective_to: date | None
    created_by_user_id: UUID
    created_at: datetime
    updated_at: datetime


class PolicyVersionWithRules(PolicyVersionResponse):
    """Active policy selection result including thresholds and document requirements."""

    thresholds: list[PolicyThresholdConfigResponse]
    doc_requirements: list[PolicyDocumentRequirementResponse]


class IngestPolicyChunksRequest(BaseModel):
    markdown_text: str = Field(min_length=1)


class PolicyChunksIngestResponse(BaseModel):
    policy_version_id: UUID
    chunks: list[PolicyChunk]
