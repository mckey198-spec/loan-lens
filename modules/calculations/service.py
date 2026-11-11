"""Financial calculation orchestration (fetch → compute → persist)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from modules.calculations import repository
from modules.calculations.engine import compute_financial_metrics
from modules.calculations.schemas import (
    CalculationInputs,
    CalculationResult,
    FinancialCalculationRecord,
    FinancialCalculationResponse,
)


def _to_calculation_response(row: dict[str, Any]) -> FinancialCalculationResponse:
    details = row.get("calculation_details_json")
    if not isinstance(details, dict):
        details = {}
    return FinancialCalculationResponse(
        id=row["id"],
        application_id=row["application_id"],
        processing_run_id=row["processing_run_id"],
        input_snapshot_id=row["input_snapshot_id"],
        proposed_emi=row["proposed_emi"],
        total_obligations=row["total_obligations"],
        foir_pct=row["foir_pct"],
        foir_threshold_pct=row["foir_threshold_pct"],
        foir_passed=row["foir_passed"],
        net_disposable_income=row["net_disposable_income"],
        max_qualifying_loan_amount=row["max_qualifying_loan_amount"],
        calculation_details=details,
        created_at=row["created_at"],
    )


async def fetch_calculation_inputs(
    connection: Any,
    input_snapshot_id: UUID,
) -> CalculationInputs:
    """Service wrapper: load frozen inputs for ``input_snapshot_id``."""
    return await repository.fetch_calculation_inputs(
        connection, input_snapshot_id
    )


async def record_financial_calculation(
    connection: Any,
    application_id: UUID,
    processing_run_id: UUID,
    input_snapshot_id: UUID,
    result: CalculationResult,
    claim_fence: int,
) -> FinancialCalculationRecord:
    """Service wrapper: persist calculated metrics with active claim fence."""
    row = await repository.record_financial_calculation(
        connection,
        application_id,
        processing_run_id,
        input_snapshot_id,
        result,
        claim_fence,
    )
    return FinancialCalculationRecord.model_validate(row)


async def run_financial_calculation(
    connection: Any,
    *,
    application_id: UUID,
    processing_run_id: UUID,
    input_snapshot_id: UUID,
    claim_fence: int,
) -> tuple[CalculationInputs, CalculationResult, FinancialCalculationRecord]:
    """
    Load snapshot inputs, compute deterministic metrics, and persist the row.

    Intended to run inside the caller's open transaction.
    """
    inputs = await fetch_calculation_inputs(connection, input_snapshot_id)
    result = compute_financial_metrics(inputs)
    record = await record_financial_calculation(
        connection,
        application_id,
        processing_run_id,
        input_snapshot_id,
        result,
        claim_fence,
    )
    return inputs, result, record


async def fetch_latest_financial_calculation(
    connection: Any,
    application_id: UUID,
) -> FinancialCalculationResponse | None:
    """
    Read-only: latest financial calculation for the application.

    Returns None when no rows exist. Does not recalculate or mutate.
    """
    row = await repository.fetch_latest_financial_calculation(
        connection, application_id
    )
    if row is None:
        return None
    return _to_calculation_response(row)


async def fetch_financial_calculation_by_run_id(
    connection: Any,
    application_id: UUID,
    run_id: UUID,
) -> FinancialCalculationResponse | None:
    """
    Read-only: calculation for a processing run, scoped to ``application_id``.

    Returns None when missing or when ``run_id`` belongs to another application.
    """
    row = await repository.fetch_financial_calculation_by_run_id(
        connection, application_id, run_id
    )
    if row is None:
        return None
    return _to_calculation_response(row)
