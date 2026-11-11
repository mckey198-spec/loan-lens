"""Financial calculations query routes (M09-T03; read-only)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from psycopg_pool import AsyncConnectionPool

from modules.calculations.schemas import FinancialCalculationResponse
from modules.calculations.service import (
    fetch_financial_calculation_by_run_id,
    fetch_latest_financial_calculation,
)
from modules.identity.deps import get_pool
from modules.identity.rbac import require_role
from modules.identity.schemas import UserProfile
from shared.errors import AppError

router = APIRouter(
    prefix="/api/v1/applications/{application_id}/calculations",
    tags=["calculations"],
)

require_calculations_reader = require_role("loan_officer", "reviewer", "admin")


@router.get("", response_model=FinancialCalculationResponse)
async def get_latest_calculation_endpoint(
    application_id: UUID,
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    _user: Annotated[UserProfile, Depends(require_calculations_reader)],
) -> FinancialCalculationResponse:
    async with pool.connection() as conn:
        calculation = await fetch_latest_financial_calculation(
            conn, application_id
        )
    if calculation is None:
        raise AppError(
            code="FINANCIAL_CALCULATION_NOT_FOUND",
            message="No financial calculation found for this application.",
            status_code=404,
            details={"application_id": str(application_id)},
        )
    return calculation


@router.get("/runs/{run_id}", response_model=FinancialCalculationResponse)
async def get_calculation_by_run_endpoint(
    application_id: UUID,
    run_id: UUID,
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    _user: Annotated[UserProfile, Depends(require_calculations_reader)],
) -> FinancialCalculationResponse:
    async with pool.connection() as conn:
        calculation = await fetch_financial_calculation_by_run_id(
            conn, application_id, run_id
        )
    if calculation is None:
        raise AppError(
            code="FINANCIAL_CALCULATION_NOT_FOUND",
            message="Financial calculation not found for this application.",
            status_code=404,
            details={
                "application_id": str(application_id),
                "run_id": str(run_id),
            },
        )
    return calculation
