"""Assessment PDF export routes (PRD §16; Architecture §10 / §11)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from psycopg_pool import AsyncConnectionPool

from modules.identity.deps import get_pool
from modules.identity.rbac import require_role
from modules.identity.schemas import UserProfile
from modules.reporting.service import generate_assessment_report
from shared.errors import request_id_from_request

router = APIRouter(prefix="/api/v1/applications", tags=["reporting"])

require_export_roles = require_role("reviewer", "loan_officer", "admin")


@router.get("/{application_id}/assessment/export")
async def export_assessment_pdf_endpoint(
    application_id: UUID,
    request: Request,
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    actor: Annotated[UserProfile, Depends(require_export_roles)],
) -> Response:
    """Stream the underwriting assessment PDF for an exportable application."""
    async with pool.connection() as conn:
        async with conn.transaction():
            pdf_bytes = await generate_assessment_report(
                conn,
                application_id,
                actor.id,
                request_id=request_id_from_request(request),
            )

    filename = f"loanlens_assessment_{application_id}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )
