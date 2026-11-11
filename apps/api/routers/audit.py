"""Admin audit integrity routes (M15-T01; Database Design §4.8)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from psycopg_pool import AsyncConnectionPool

from modules.governance.verifier import (
    AuditVerificationReport,
    verify_audit_log_integrity,
)
from modules.identity.deps import get_pool
from modules.identity.rbac import require_admin
from modules.identity.schemas import UserProfile

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


@router.get("/verify", response_model=AuditVerificationReport)
async def verify_audit_chain_endpoint(
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    _admin: Annotated[UserProfile, Depends(require_admin)],
) -> AuditVerificationReport:
    """Admin-only read-only walk of the global audit hash chain."""
    async with pool.connection() as conn:
        return await verify_audit_log_integrity(conn)
