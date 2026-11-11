"""Raw SQL persistence for calculation inputs and financial results."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from modules.calculations.schemas import CalculationInputs, CalculationResult

_CALC_INPUT_COLUMNS = """
    monthly_gross_income,
    monthly_net_income,
    monthly_obligations,
    requested_loan_amount,
    tenure_months,
    annual_interest_rate_pct,
    max_allowed_foir_pct
"""

_FIN_CALC_RETURNING = """
    id,
    application_id,
    processing_run_id,
    input_snapshot_id,
    proposed_emi,
    total_obligations,
    foir_pct,
    foir_threshold_pct,
    foir_passed,
    net_disposable_income,
    max_qualifying_loan_amount,
    calculation_details_json,
    claim_fence,
    created_at
"""

_FIN_CALC_SELECT = """
    id,
    application_id,
    processing_run_id,
    input_snapshot_id,
    proposed_emi,
    total_obligations,
    foir_pct,
    foir_threshold_pct,
    foir_passed,
    net_disposable_income,
    max_qualifying_loan_amount,
    calculation_details_json,
    created_at
"""


def _as_decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


async def fetch_calculation_inputs(
    connection: Any,
    input_snapshot_id: UUID,
) -> CalculationInputs:
    """Load frozen calculation inputs for the given input snapshot."""
    row = await connection.execute(
        f"""
        SELECT {_CALC_INPUT_COLUMNS}
          FROM snapshot_calculation_inputs
         WHERE input_snapshot_id = %s
        """,
        (input_snapshot_id,),
    )
    record = await row.fetchone()
    if record is None:
        raise ValueError(
            f"snapshot_calculation_inputs missing for input_snapshot_id="
            f"{input_snapshot_id}"
        )
    return CalculationInputs(
        monthly_gross_income=_as_decimal(record["monthly_gross_income"]),
        monthly_net_income=_as_decimal(record["monthly_net_income"]),
        monthly_obligations=_as_decimal(record["monthly_obligations"]),
        requested_loan_amount=_as_decimal(record["requested_loan_amount"]),
        tenure_months=int(record["tenure_months"]),
        annual_interest_rate_pct=_as_decimal(
            record["annual_interest_rate_pct"]
        ),
        max_allowed_foir_pct=_as_decimal(record["max_allowed_foir_pct"]),
    )


async def record_financial_calculation(
    connection: Any,
    application_id: UUID,
    processing_run_id: UUID,
    input_snapshot_id: UUID,
    result: CalculationResult,
    claim_fence: int,
    *,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    """
    Persist an immutable financial calculation row.

    ``claim_fence`` must be the worker's active fence for the processing run.
    """
    if claim_fence < 0:
        raise ValueError("claim_fence must be >= 0")

    row = await connection.execute(
        f"""
        INSERT INTO application_financial_calculations (
            application_id,
            processing_run_id,
            input_snapshot_id,
            proposed_emi,
            total_obligations,
            foir_pct,
            foir_threshold_pct,
            foir_passed,
            net_disposable_income,
            max_qualifying_loan_amount,
            calculation_details_json,
            claim_fence,
            created_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            COALESCE(%s, now())
        )
        RETURNING {_FIN_CALC_RETURNING}
        """,
        (
            application_id,
            processing_run_id,
            input_snapshot_id,
            result.proposed_emi,
            result.total_obligations,
            result.foir_pct,
            result.foir_threshold_pct,
            result.foir_passed,
            result.net_disposable_income,
            result.max_qualifying_loan_amount,
            Jsonb(result.calculation_details),
            claim_fence,
            created_at,
        ),
    )
    inserted = await row.fetchone()
    assert inserted is not None
    return dict(inserted)


async def fetch_latest_financial_calculation(
    connection: Any,
    application_id: UUID,
) -> dict[str, Any] | None:
    """
    Read-only: latest calculation row for ``application_id``.

    Always filters on ``application_id`` (tenant isolation). SELECT only.
    """
    row = await connection.execute(
        f"""
        SELECT {_FIN_CALC_SELECT}
          FROM application_financial_calculations
         WHERE application_id = %s
         ORDER BY created_at DESC, id DESC
         LIMIT 1
        """,
        (application_id,),
    )
    result = await row.fetchone()
    return dict(result) if result is not None else None


async def fetch_financial_calculation_by_run_id(
    connection: Any,
    application_id: UUID,
    run_id: UUID,
) -> dict[str, Any] | None:
    """
    Read-only: calculation for ``processing_run_id`` scoped to ``application_id``.

    Returns None when missing or when the run belongs to another application.
    """
    row = await connection.execute(
        f"""
        SELECT {_FIN_CALC_SELECT}
          FROM application_financial_calculations
         WHERE application_id = %s
           AND processing_run_id = %s
        """,
        (application_id, run_id),
    )
    result = await row.fetchone()
    return dict(result) if result is not None else None
