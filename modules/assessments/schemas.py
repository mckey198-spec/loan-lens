"""Credit reviewer assessment, disposition, and correction schemas (M12-T03)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

AssessmentRecommendation = Literal[
    "approve",
    "reject",
    "refer",
    "exception_approved",
]

FindingDispositionAction = Literal[
    "accepted_risk",
    "overridden",
    "confirmed_issue",
    "false_positive",
]


def _require_min_trimmed_length(value: str, *, minimum: int) -> str:
    stripped = value.strip()
    if len(stripped) < minimum:
        raise ValueError(f"must be at least {minimum} characters after trimming")
    return stripped


class AssessmentCreate(BaseModel):
    """Internal create payload used when claiming an application for review."""

    model_config = ConfigDict(frozen=True)

    application_id: UUID
    processing_run_id: UUID
    input_snapshot_id: UUID
    applicable_policy_version_id: UUID
    assigned_reviewer_id: UUID


class AssessmentDecisionRequest(BaseModel):
    """Reviewer final decision on an assessment."""

    model_config = ConfigDict(frozen=True)

    recommendation: AssessmentRecommendation
    reviewer_notes: str = Field(min_length=10)

    @field_validator("reviewer_notes")
    @classmethod
    def trim_reviewer_notes(cls, value: str) -> str:
        return _require_min_trimmed_length(value, minimum=10)


class AssessmentResponse(BaseModel):
    """Persisted assessments row projection."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    application_id: UUID
    processing_run_id: UUID
    input_snapshot_id: UUID
    applicable_policy_version_id: UUID
    assigned_reviewer_id: UUID | None
    recommendation: AssessmentRecommendation | None
    reviewer_notes: str | None
    decided_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AssessmentListResponse(BaseModel):
    """List of assessments for an application."""

    model_config = ConfigDict(frozen=True)

    items: list[AssessmentResponse]
    total: int


class FindingDispositionRequest(BaseModel):
    """Append-only disposition of a verification finding."""

    model_config = ConfigDict(frozen=True)

    finding_id: UUID
    action: FindingDispositionAction
    justification: str = Field(min_length=10)

    @field_validator("justification")
    @classmethod
    def trim_justification(cls, value: str) -> str:
        return _require_min_trimmed_length(value, minimum=10)


class FindingDispositionResponse(BaseModel):
    """Persisted finding_dispositions row projection."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    application_id: UUID
    assessment_id: UUID
    verification_finding_id: UUID
    action: FindingDispositionAction
    justification: str
    created_by_user_id: UUID
    created_at: datetime


class FieldCorrectionRequest(BaseModel):
    """Typed Layer-4 field correction (does not overwrite extraction rows)."""

    model_config = ConfigDict(frozen=True)

    extracted_field_id: UUID
    corrected_value_text: str | None = None
    corrected_value_numeric: Decimal | None = Field(
        default=None, max_digits=18, decimal_places=2
    )
    corrected_value_date: date | None = None
    reason: str = Field(min_length=10)

    @field_validator("reason")
    @classmethod
    def trim_reason(cls, value: str) -> str:
        return _require_min_trimmed_length(value, minimum=10)

    @field_validator("corrected_value_text")
    @classmethod
    def trim_text_value(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped if stripped else None

    @model_validator(mode="after")
    def require_at_least_one_corrected_value(self) -> FieldCorrectionRequest:
        if (
            self.corrected_value_text is None
            and self.corrected_value_numeric is None
            and self.corrected_value_date is None
        ):
            raise ValueError(
                "at least one of corrected_value_text, "
                "corrected_value_numeric, corrected_value_date is required"
            )
        return self


class FieldCorrectionResponse(BaseModel):
    """Persisted field_corrections row projection."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    application_id: UUID
    extracted_field_id: UUID
    assessment_id: UUID | None
    corrected_value_text: str | None
    corrected_value_numeric: Decimal | None
    corrected_value_date: date | None
    reason: str
    corrected_by_user_id: UUID
    created_at: datetime
