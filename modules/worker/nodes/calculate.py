"""Deterministic financial calculation pipeline node (Architecture §8)."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from psycopg_pool import AsyncConnectionPool

from modules.calculations.service import run_financial_calculation

logger = logging.getLogger("loanlens.worker")

WorkflowState = dict[str, Any]


def _as_uuid(value: object, field: str) -> UUID:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str) and value:
        return UUID(value)
    raise ValueError(f"workflow state missing valid {field}")


def _as_fence(value: object) -> int:
    if isinstance(value, bool) or value is None:
        raise ValueError("workflow state missing valid claim_fence")
    return int(value)


async def calculate_financials(state: WorkflowState) -> WorkflowState:
    """
    Pipeline node: load ``snapshot_calculation_inputs``, compute metrics,
    and insert ``application_financial_calculations`` with the active fence.
    """
    pool = state.get("_db_pool")
    if not isinstance(pool, AsyncConnectionPool):
        raise ValueError("calculate_financials requires _db_pool in workflow state")

    application_id = _as_uuid(state.get("application_id"), "application_id")
    processing_run_id = _as_uuid(
        state.get("processing_run_id"),
        "processing_run_id",
    )
    claim_fence = _as_fence(state.get("claim_fence"))

    input_snapshot_raw = state.get("input_snapshot_id")
    if input_snapshot_raw is None or input_snapshot_raw == "":
        async with pool.connection() as conn:
            run_row = await conn.execute(
                """
                SELECT input_snapshot_id
                  FROM processing_runs
                 WHERE id = %s
                   AND application_id = %s
                """,
                (processing_run_id, application_id),
            )
            run = await run_row.fetchone()
            await conn.commit()
        if run is None or run["input_snapshot_id"] is None:
            raise ValueError("calculate_financials requires input_snapshot_id")
        input_snapshot_id = run["input_snapshot_id"]
    else:
        input_snapshot_id = _as_uuid(input_snapshot_raw, "input_snapshot_id")

    async with pool.connection() as conn:
        async with conn.transaction():
            _inputs, result, record = await run_financial_calculation(
                conn,
                application_id=application_id,
                processing_run_id=processing_run_id,
                input_snapshot_id=input_snapshot_id,
                claim_fence=claim_fence,
            )

    logger.info(
        "financial_calculation_completed",
        extra={
            "application_id": str(application_id),
            "processing_run_id": str(processing_run_id),
            "input_snapshot_id": str(input_snapshot_id),
            "claim_fence": claim_fence,
            "financial_calculation_id": str(record.id),
            "proposed_emi": str(result.proposed_emi),
            "foir_pct": str(result.foir_pct),
            "foir_passed": result.foir_passed,
        },
    )

    updated = dict(state)
    updated.pop("_db_pool", None)
    updated["input_snapshot_id"] = str(input_snapshot_id)
    updated["financial_calculation_id"] = str(record.id)
    updated["financial_calculation"] = {
        "proposed_emi": str(result.proposed_emi),
        "total_obligations": str(result.total_obligations),
        "foir_pct": str(result.foir_pct),
        "foir_threshold_pct": str(result.foir_threshold_pct),
        "foir_passed": result.foir_passed,
        "net_disposable_income": str(result.net_disposable_income),
        "max_qualifying_loan_amount": str(result.max_qualifying_loan_amount),
        "calculation_details": result.calculation_details,
    }
    return updated
