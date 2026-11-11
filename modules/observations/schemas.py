"""AI observation context and output schemas (Architecture §8; BR-12)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SnapshotFactSummary(BaseModel):
    """Minimal frozen snapshot facts used as observation context."""

    model_config = ConfigDict(frozen=True)

    applicant_type: str | None = None
    requested_amount: Decimal | None = Field(
        default=None, max_digits=18, decimal_places=2
    )
    tenure_months: int | None = Field(default=None, ge=0)
    declared_net_monthly_income: Decimal | None = Field(
        default=None, max_digits=18, decimal_places=2
    )
    declared_existing_emis: Decimal | None = Field(
        default=None, max_digits=18, decimal_places=2
    )
    as_of_date: str | None = None


class RuleFindingSummary(BaseModel):
    """One BR-01..BR-11 verification finding summarized for the provider."""

    model_config = ConfigDict(frozen=True)

    rule_code: str
    rule_name: str | None = None
    status: str
    severity: str
    message: str


class CalculationMetricsSummary(BaseModel):
    """Deterministic affordability metrics from the calculation engine."""

    model_config = ConfigDict(frozen=True)

    proposed_emi: Decimal | None = Field(
        default=None, max_digits=18, decimal_places=2
    )
    total_obligations: Decimal | None = Field(
        default=None, max_digits=18, decimal_places=2
    )
    foir_pct: Decimal | None = Field(
        default=None, max_digits=5, decimal_places=2
    )
    foir_threshold_pct: Decimal | None = Field(
        default=None, max_digits=5, decimal_places=2
    )
    foir_passed: bool | None = None
    net_disposable_income: Decimal | None = Field(
        default=None, max_digits=18, decimal_places=2
    )
    max_qualifying_loan_amount: Decimal | None = Field(
        default=None, max_digits=18, decimal_places=2
    )


class PolicyCitationSummary(BaseModel):
    """One policy chunk citation available for grounding observations."""

    model_config = ConfigDict(frozen=True)

    chunk_id: UUID
    topic: str | None = None
    section_label: str | None = None
    rank_score: float | None = None


class ObservationContext(BaseModel):
    """
    Inputs assembled for observation generation (BR-12).

    Contains snapshot facts, deterministic rule findings (BR-01..11),
    calculation metrics, and policy citations. Never includes an
    approval/rejection outcome or risk grade.
    """

    model_config = ConfigDict(frozen=True)

    application_id: UUID
    processing_run_id: UUID
    snapshot: SnapshotFactSummary = Field(default_factory=SnapshotFactSummary)
    findings: list[RuleFindingSummary] = Field(default_factory=list)
    calculation: CalculationMetricsSummary | None = None
    policy_citations: list[PolicyCitationSummary] = Field(default_factory=list)


class ObservationOutput(BaseModel):
    """Structured advisory observation produced by an ObservationProvider."""

    model_config = ConfigDict(frozen=True)

    observation_text: str = Field(min_length=1)
    risk_factors: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    cited_chunk_ids: list[UUID] = Field(default_factory=list)
    model_id: str = Field(min_length=1)

    @field_validator("observation_text")
    @classmethod
    def _strip_observation_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("observation_text must be non-empty")
        return cleaned


class AiObservationRecord(BaseModel):
    """Persisted ``assessment_ai_observations`` row."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    application_id: UUID
    processing_run_id: UUID
    model_id: str
    observation_text: str
    risk_factors_json: list[Any]
    strengths_json: list[Any]
    cited_chunk_ids_json: list[Any]
    is_ai_generated: bool
    claim_fence: int
    created_at: datetime


class AiObservationResponse(BaseModel):
    """Read-only API projection of an AI observation (explicitly labelled)."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    application_id: UUID
    processing_run_id: UUID
    model_id: str
    observation_text: str
    risk_factors: list[str]
    strengths: list[str]
    cited_chunk_ids: list[UUID]
    is_ai_generated: bool = True
    created_at: datetime
