"""Verification rule context and evaluation result schemas (M08-T02)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

RuleStatus = Literal["passed", "flagged", "failed"]
RuleSeverity = Literal["info", "low", "medium", "high", "critical"]
ProvenanceLayer = Literal["corrected", "normalized", "raw", "snapshot", "policy"]

NAME_MATCH_MIN_RATIO = Decimal("0.85")
SALARY_CREDIT_TOLERANCE_PCT = Decimal("10")
INCOME_DISCREPANCY_TOLERANCE_PCT = Decimal("5")


class PolicyThresholds(BaseModel):
    """Versioned policy thresholds consumed by BR-07 / BR-08 (not hard-coded)."""

    model_config = ConfigDict(frozen=True)

    min_employment_months: int = Field(ge=0)
    min_monthly_income: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    max_foir_pct: Decimal | None = Field(
        default=None, gt=0, le=100, max_digits=5, decimal_places=2
    )
    max_loan_amount: Decimal | None = Field(
        default=None, gt=0, max_digits=18, decimal_places=2
    )


class RuleTolerances(BaseModel):
    """Numeric tolerances passed into rules (policy/config arguments)."""

    model_config = ConfigDict(frozen=True)

    name_match_min_ratio: Decimal = NAME_MATCH_MIN_RATIO
    employer_match_min_ratio: Decimal = NAME_MATCH_MIN_RATIO
    salary_credit_tolerance_pct: Decimal = SALARY_CREDIT_TOLERANCE_PCT
    income_discrepancy_tolerance_pct: Decimal = INCOME_DISCREPANCY_TOLERANCE_PCT


class SnapshotFacts(BaseModel):
    """Frozen declared facts used as rule inputs."""

    model_config = ConfigDict(frozen=True)

    applicant_full_name: str
    date_of_birth: date
    pan: str
    employer_or_business_name: str
    time_in_job_or_business_months: int = Field(ge=0)
    declared_gross_monthly_income: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    declared_net_monthly_income: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    declared_existing_emis: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    applicant_type: Literal["salaried", "self_employed"]
    as_of_date: date
    document_inventory: list[dict[str, Any]] = Field(default_factory=list)


class LayeredField(BaseModel):
    """
    One extracted field with optional Layer-4 correction overlay.

    Resolution order (Architecture lineage): Layer 4 corrected → Layer 2
    normalized → Layer 1 raw.
    """

    model_config = ConfigDict(frozen=True)

    field_name: str
    document_type: str | None = None
    source_document_id: UUID | None = None
    extraction_id: UUID | None = None
    raw_value: str | None = None
    normalized_value: dict[str, Any] | None = None
    corrected_value: dict[str, Any] | None = None

    def effective_payload(self) -> tuple[dict[str, Any] | None, ProvenanceLayer | None]:
        if self.corrected_value is not None:
            return self.corrected_value, "corrected"
        if self.normalized_value is not None:
            return self.normalized_value, "normalized"
        if self.raw_value is not None:
            return {"value": self.raw_value}, "raw"
        return None, None

    def effective_text(self) -> tuple[str | None, ProvenanceLayer | None]:
        payload, layer = self.effective_payload()
        if payload is None or layer is None:
            return None, None
        for key in ("value", "amount", "text"):
            if key in payload and payload[key] is not None:
                return str(payload[key]), layer
        return None, layer


class SalaryCreditObservation(BaseModel):
    """Bank-identified salary credit used by BR-05."""

    model_config = ConfigDict(frozen=True)

    statement_month: date
    amount: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    source_document_id: UUID | None = None


class StatementPeriod(BaseModel):
    """One bank-statement coverage window used by BR-09."""

    model_config = ConfigDict(frozen=True)

    start_date: date
    end_date: date
    source_document_id: UUID | None = None

    @field_validator("end_date")
    @classmethod
    def _end_on_or_after_start(cls, value: date, info: ValidationInfo) -> date:
        start = info.data.get("start_date")
        if isinstance(start, date) and value < start:
            raise ValueError("end_date must be >= start_date")
        return value


class RecurringDebitObservation(BaseModel):
    """Recurring debit pattern used by BR-10 EMI corroboration."""

    model_config = ConfigDict(frozen=True)

    label: str
    monthly_amount: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    occurrence_count: int = Field(ge=1)
    source_document_id: UUID | None = None


class MandatoryDocumentRequirement(BaseModel):
    """Policy checklist entry for BR-11."""

    model_config = ConfigDict(frozen=True)

    document_type: str
    is_mandatory: bool = True
    min_count: int = Field(default=1, ge=0)


class RuleEvaluationContext(BaseModel):
    """
    Pure inputs for BR-01…BR-11. No database handles, no LLM clients.
    """

    model_config = ConfigDict(frozen=True)

    snapshot: SnapshotFacts
    fields: list[LayeredField] = Field(default_factory=list)
    salary_slip_net_pays: list[Decimal] = Field(default_factory=list)
    salary_credits: list[SalaryCreditObservation] = Field(default_factory=list)
    statement_periods: list[StatementPeriod] = Field(default_factory=list)
    recurring_debits: list[RecurringDebitObservation] = Field(default_factory=list)
    mandatory_documents: list[MandatoryDocumentRequirement] = Field(
        default_factory=list
    )
    policy_thresholds: PolicyThresholds
    tolerances: RuleTolerances = Field(default_factory=RuleTolerances)


class RuleEvaluationResult(BaseModel):
    """Immutable draft finding produced by one deterministic rule."""

    model_config = ConfigDict(frozen=True)

    rule_code: str
    rule_name: str
    status: RuleStatus
    severity: RuleSeverity
    message: str
    diff_payload: dict[str, Any] = Field(default_factory=dict)
    provenance_sources: list[dict[str, Any]] = Field(default_factory=list)


class VerificationRunRecord(BaseModel):
    """Persisted verification_runs row."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    application_id: UUID
    processing_run_id: UUID
    input_snapshot_id: UUID
    total_rules_evaluated: int
    passed_count: int
    flagged_count: int
    failed_count: int
    created_at: datetime


class VerificationFindingRecord(BaseModel):
    """Persisted verification_findings row."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    application_id: UUID
    verification_run_id: UUID
    rule_code: str
    rule_name: str
    status: RuleStatus
    severity: RuleSeverity
    message: str
    diff_payload_json: dict[str, Any]
    provenance_sources_json: list[dict[str, Any]]
    claim_fence: int
    created_at: datetime


class VerificationFindingResponse(BaseModel):
    """Read-only finding payload for the verification query API (M08-T03)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    rule_code: str
    rule_name: str
    status: RuleStatus
    severity: RuleSeverity
    message: str
    diff_payload: dict[str, Any] = Field(default_factory=dict)
    provenance_sources: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime


class VerificationRunSummaryResponse(BaseModel):
    """Read-only verification run with ordered findings (M08-T03)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    application_id: UUID
    processing_run_id: UUID
    total_rules_evaluated: int
    passed_count: int
    flagged_count: int
    failed_count: int
    created_at: datetime
    findings: list[VerificationFindingResponse] = Field(default_factory=list)
