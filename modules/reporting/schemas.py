"""Assessment package schemas for deterministic PDF export (M13 / Architecture §10)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


BR12_ADVISORY_BANNER = (
    "ADVISORY OBSERVATIONS ONLY (BR-12) - NOT AN AUTOMATED CREDIT DECISION"
)

EXPORTABLE_APPLICATION_STATUSES: frozenset[str] = frozenset(
    {
        "in_review",
        "review_completed",
        "escalated",
    }
)


class SnapshotMetadata(BaseModel):
    """Frozen application input snapshot header for Section 1."""

    model_config = ConfigDict(frozen=True)

    snapshot_id: UUID
    snapshot_hash: str
    as_of_date: date
    policy_version_id: UUID
    policy_version_label: str
    application_reference_code: str
    application_status: str


class AssessmentDecisionBlock(BaseModel):
    """Final assessment decision for Section 2."""

    model_config = ConfigDict(frozen=True)

    assessment_id: UUID
    recommendation: str | None
    reviewer_user_id: UUID | None
    reviewer_full_name: str | None
    decided_at: datetime | None
    reviewer_notes: str | None


class FinancialMetricsBlock(BaseModel):
    """Calculation inputs and outputs for Section 3."""

    model_config = ConfigDict(frozen=True)

    monthly_gross_income: Decimal | None = None
    monthly_net_income: Decimal | None = None
    monthly_obligations: Decimal | None = None
    proposed_emi: Decimal | None = None
    total_obligations: Decimal | None = None
    foir_pct: Decimal | None = None
    foir_threshold_pct: Decimal | None = None
    foir_passed: bool | None = None
    max_qualifying_loan_amount: Decimal | None = None
    net_disposable_income: Decimal | None = None


class RuleFindingRow(BaseModel):
    """One deterministic BR-01..BR-11 finding for Section 4."""

    model_config = ConfigDict(frozen=True)

    rule_code: str
    rule_name: str
    status: str
    severity: str
    message: str
    diff_payload_json: dict[str, Any] = Field(default_factory=dict)


class DispositionRow(BaseModel):
    """Finding disposition for Section 5."""

    model_config = ConfigDict(frozen=True)

    rule_code: str | None
    action: str
    justification: str
    created_by_user_id: UUID
    created_at: datetime


class FieldCorrectionRow(BaseModel):
    """Typed field correction for Section 5."""

    model_config = ConfigDict(frozen=True)

    field_name: str | None
    corrected_value_text: str | None
    corrected_value_numeric: Decimal | None
    corrected_value_date: date | None
    reason: str
    corrected_by_user_id: UUID
    created_at: datetime


class PolicyCitationRow(BaseModel):
    """Lexical policy citation for Section 6."""

    model_config = ConfigDict(frozen=True)

    topic: str
    section_label: str
    content_text: str


class AiObservationRow(BaseModel):
    """Labelled AI observation for Section 7 (BR-12 advisory only)."""

    model_config = ConfigDict(frozen=True)

    model_id: str
    observation_text: str
    risk_factors: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    is_ai_generated: bool = True
    created_at: datetime


class AssessmentReportPackage(BaseModel):
    """Read-only assessment package assembled for PDF rendering."""

    model_config = ConfigDict(frozen=True)

    application_id: UUID
    snapshot: SnapshotMetadata
    decision: AssessmentDecisionBlock
    financials: FinancialMetricsBlock
    findings: list[RuleFindingRow] = Field(default_factory=list)
    dispositions: list[DispositionRow] = Field(default_factory=list)
    corrections: list[FieldCorrectionRow] = Field(default_factory=list)
    policy_citations: list[PolicyCitationRow] = Field(default_factory=list)
    ai_observations: list[AiObservationRow] = Field(default_factory=list)
