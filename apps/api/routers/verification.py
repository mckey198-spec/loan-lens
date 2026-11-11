"""Verification findings query routes (M08-T03; read-only)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from psycopg_pool import AsyncConnectionPool

from modules.identity.deps import get_pool
from modules.identity.rbac import require_role
from modules.identity.schemas import UserProfile
from modules.verification.schemas import VerificationRunSummaryResponse
from modules.verification.service import (
    get_latest_verification_run,
    get_verification_run_by_id,
)
from shared.errors import AppError

router = APIRouter(
    prefix="/api/v1/applications/{application_id}/verification",
    tags=["verification"],
)

require_verification_reader = require_role("loan_officer", "reviewer", "admin")


@router.get("", response_model=VerificationRunSummaryResponse)
async def get_latest_verification_endpoint(
    application_id: UUID,
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    _user: Annotated[UserProfile, Depends(require_verification_reader)],
) -> VerificationRunSummaryResponse:
    async with pool.connection() as conn:
        summary = await get_latest_verification_run(conn, application_id)
    if summary is None:
        raise AppError(
            code="VERIFICATION_RUN_NOT_FOUND",
            message="No verification run found for this application.",
            status_code=404,
            details={"application_id": str(application_id)},
        )
    return summary


@router.get("/runs/{run_id}", response_model=VerificationRunSummaryResponse)
async def get_verification_run_endpoint(
    application_id: UUID,
    run_id: UUID,
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    _user: Annotated[UserProfile, Depends(require_verification_reader)],
) -> VerificationRunSummaryResponse:
    async with pool.connection() as conn:
        summary = await get_verification_run_by_id(conn, application_id, run_id)
    if summary is None:
        raise AppError(
            code="VERIFICATION_RUN_NOT_FOUND",
            message="Verification run not found for this application.",
            status_code=404,
            details={
                "application_id": str(application_id),
                "run_id": str(run_id),
            },
        )
    return summary
