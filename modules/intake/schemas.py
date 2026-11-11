"""Intake API schemas for draft applications and applicants (M03-T02 / M03-T03)."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ApplicantType = Literal["salaried", "self_employed"]
ApplicationStatus = Literal[
    "draft",
    "submitted",
    "processing",
    "processing_failed",
    "awaiting_human_review",
    "in_review",
    "clarification_required",
    "escalated",
    "review_completed",
    "archived",
]

_PAN_PATTERN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")


def _normalize_pan(value: str) -> str:
    normalized = value.strip().upper()
    if not _PAN_PATTERN.fullmatch(normalized):
        raise ValueError("pan must match ^[A-Z]{5}[0-9]{4}[A-Z]$")
    return normalized


def require_loan_purpose_other(
    loan_purpose: str,
    loan_purpose_other: str | None,
) -> str | None:
    purpose = loan_purpose.strip()
    if purpose != "other":
        return loan_purpose_other
    if loan_purpose_other is None or not loan_purpose_other.strip():
        raise ValueError("loan_purpose_other is required when loan_purpose is 'other'")
    return loan_purpose_other.strip()


class ApplicantCreate(BaseModel):
    """Required declared applicant fields plus optional money amounts (null stays null)."""

    full_name: str = Field(min_length=1, max_length=512)
    date_of_birth: date
    pan: str = Field(min_length=10, max_length=10)
    current_address: str = Field(min_length=1, max_length=4000)
    employer_or_business_name: str = Field(min_length=1, max_length=512)
    time_in_job_or_business_months: int = Field(ge=0)
    gross_monthly_income: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    net_monthly_income: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    total_existing_monthly_emis: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    other_monthly_income: Decimal | None = Field(
        default=None, ge=0, max_digits=18, decimal_places=2
    )
    other_monthly_income_evidenced: bool = False
    monthly_household_expenses: Decimal | None = Field(
        default=None, ge=0, max_digits=18, decimal_places=2
    )
    outstanding_existing_loan_amount: Decimal | None = Field(
        default=None, ge=0, max_digits=18, decimal_places=2
    )

    @field_validator("full_name", "current_address", "employer_or_business_name")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @field_validator("pan")
    @classmethod
    def validate_pan(cls, value: str) -> str:
        return _normalize_pan(value)

    @model_validator(mode="after")
    def net_must_not_exceed_gross(self) -> ApplicantCreate:
        if self.net_monthly_income > self.gross_monthly_income:
            raise ValueError("net_monthly_income must be <= gross_monthly_income")
        return self


class CreateApplicationRequest(BaseModel):
    """Create a draft application and its applicant in one request."""

    applicant_type: ApplicantType
    requested_amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    tenure_months: int = Field(gt=0)
    loan_purpose: str = Field(min_length=1, max_length=128)
    loan_purpose_other: str | None = None
    applicant: ApplicantCreate

    @field_validator("loan_purpose")
    @classmethod
    def strip_loan_purpose(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("loan_purpose must not be blank")
        return stripped

    @model_validator(mode="after")
    def validate_purpose_other(self) -> CreateApplicationRequest:
        self.loan_purpose_other = require_loan_purpose_other(
            self.loan_purpose,
            self.loan_purpose_other,
        )
        return self


class ApplicantUpdate(BaseModel):
    """Partial applicant update; omitted optional money fields are left unchanged."""

    full_name: str | None = Field(default=None, min_length=1, max_length=512)
    date_of_birth: date | None = None
    pan: str | None = Field(default=None, min_length=10, max_length=10)
    current_address: str | None = Field(default=None, min_length=1, max_length=4000)
    employer_or_business_name: str | None = Field(
        default=None, min_length=1, max_length=512
    )
    time_in_job_or_business_months: int | None = Field(default=None, ge=0)
    gross_monthly_income: Decimal | None = Field(
        default=None, ge=0, max_digits=18, decimal_places=2
    )
    net_monthly_income: Decimal | None = Field(
        default=None, ge=0, max_digits=18, decimal_places=2
    )
    total_existing_monthly_emis: Decimal | None = Field(
        default=None, ge=0, max_digits=18, decimal_places=2
    )
    other_monthly_income: Decimal | None = Field(
        default=None, ge=0, max_digits=18, decimal_places=2
    )
    other_monthly_income_evidenced: bool | None = None
    monthly_household_expenses: Decimal | None = Field(
        default=None, ge=0, max_digits=18, decimal_places=2
    )
    outstanding_existing_loan_amount: Decimal | None = Field(
        default=None, ge=0, max_digits=18, decimal_places=2
    )

    @field_validator("full_name", "current_address", "employer_or_business_name")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @field_validator("pan")
    @classmethod
    def validate_pan(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _normalize_pan(value)


class UpdateApplicationRequest(BaseModel):
    """Partial draft update. Rejected by the service when status is not draft."""

    applicant_type: ApplicantType | None = None
    requested_amount: Decimal | None = Field(
        default=None, gt=0, max_digits=18, decimal_places=2
    )
    tenure_months: int | None = Field(default=None, gt=0)
    loan_purpose: str | None = Field(default=None, min_length=1, max_length=128)
    loan_purpose_other: str | None = None
    applicant: ApplicantUpdate | None = None

    @field_validator("loan_purpose")
    @classmethod
    def strip_loan_purpose(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("loan_purpose must not be blank")
        return stripped


class ApplicantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    application_id: UUID
    full_name: str
    date_of_birth: date
    pan: str
    current_address: str
    employer_or_business_name: str
    time_in_job_or_business_months: int
    gross_monthly_income: Decimal
    net_monthly_income: Decimal
    other_monthly_income: Decimal | None
    other_monthly_income_evidenced: bool
    monthly_household_expenses: Decimal | None
    total_existing_monthly_emis: Decimal
    outstanding_existing_loan_amount: Decimal | None
    created_at: datetime
    updated_at: datetime


class ApplicationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    reference_code: str
    status: ApplicationStatus
    created_by_user_id: UUID
    assigned_reviewer_user_id: UUID | None
    applicant_type: ApplicantType
    requested_amount: Decimal
    currency: str
    tenure_months: int
    loan_purpose: str
    loan_purpose_other: str | None
    applicable_policy_version_id: UUID | None
    withdraw_reason: str | None
    submitted_at: datetime | None
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime
    applicant: ApplicantResponse


class ApplicationListResponse(BaseModel):
    items: list[ApplicationResponse]
    offset: int
    limit: int
    total: int


class WithdrawRequest(BaseModel):
    """Loan Officer or Admin withdrawal to archived (Architecture §5)."""

    reason: str = Field(min_length=1, max_length=4000)

    @field_validator("reason")
    @classmethod
    def require_non_empty_reason(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("reason must not be blank")
        return stripped


class ArchiveRequest(BaseModel):
    """Admin archival to archived (Architecture §5)."""

    reason: str = Field(min_length=1, max_length=4000)

    @field_validator("reason")
    @classmethod
    def require_non_empty_reason(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("reason must not be blank")
        return stripped


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

DocumentStatus = Literal[
    "uploaded",
    "classified",
    "extracting",
    "extracted",
    "failed",
    "unsupported",
    "replaced",
]


class DocumentResponse(BaseModel):
    """Document metadata returned to clients. Never includes storage_uri."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    application_id: UUID
    content_sha256: str
    document_type: DocumentType
    status: DocumentStatus
    original_filename: str
    mime_type: str
    byte_size: int
    is_alternative_evidence: bool
    uploaded_by_user_id: UUID
    created_at: datetime
    updated_at: datetime
