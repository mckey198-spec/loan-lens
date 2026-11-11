"""Financial calculation input/result schemas (Architecture §8; ADR-009)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CalculationInputs(BaseModel):
    """Frozen numeric inputs matching ``snapshot_calculation_inputs``."""

    model_config = ConfigDict(frozen=True)

    monthly_gross_income: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    monthly_net_income: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    monthly_obligations: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    requested_loan_amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    tenure_months: int = Field(gt=0)
    annual_interest_rate_pct: Decimal = Field(
        ge=0, le=100, max_digits=5, decimal_places=2
    )
    max_allowed_foir_pct: Decimal = Field(
        gt=0, le=100, max_digits=5, decimal_places=2
    )


class CalculationResult(BaseModel):
    """Deterministic affordability metrics produced by the pure engine."""

    model_config = ConfigDict(frozen=True)

    proposed_emi: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    total_obligations: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    foir_pct: Decimal = Field(ge=0, max_digits=5, decimal_places=2)
    foir_threshold_pct: Decimal = Field(gt=0, le=100, max_digits=5, decimal_places=2)
    foir_passed: bool
    net_disposable_income: Decimal = Field(max_digits=18, decimal_places=2)
    max_qualifying_loan_amount: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    calculation_details: dict[str, Any] = Field(default_factory=dict)


class FinancialCalculationRecord(BaseModel):
    """Persisted ``application_financial_calculations`` row."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    application_id: UUID
    processing_run_id: UUID
    input_snapshot_id: UUID
    proposed_emi: Decimal
    total_obligations: Decimal
    foir_pct: Decimal
    foir_threshold_pct: Decimal
    foir_passed: bool
    net_disposable_income: Decimal
    max_qualifying_loan_amount: Decimal
    calculation_details_json: dict[str, Any]
    claim_fence: int
    created_at: datetime


class FinancialCalculationResponse(BaseModel):
    """Read-only API projection of a financial calculation row (M09-T03)."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    application_id: UUID
    processing_run_id: UUID
    input_snapshot_id: UUID
    proposed_emi: Decimal
    total_obligations: Decimal
    foir_pct: Decimal
    foir_threshold_pct: Decimal
    foir_passed: bool
    net_disposable_income: Decimal
    max_qualifying_loan_amount: Decimal
    calculation_details: dict[str, Any]
    created_at: datetime
