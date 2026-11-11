"""Admin policy document and version routes (Architecture §9; M06-T02 / M10)."""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from psycopg_pool import AsyncConnectionPool

from modules.identity.deps import get_current_active_user, get_pool
from modules.identity.rbac import require_admin, require_role
from modules.identity.schemas import UserProfile
from modules.policy.schemas import (
    CreatePolicyDocumentRequest,
    CreatePolicyVersionRequest,
    IngestPolicyChunksRequest,
    PolicyChunksIngestResponse,
    PolicyCitationsResponse,
    PolicyDocumentResponse,
    PolicyVersionResponse,
    PolicyVersionWithRules,
)
from modules.policy.service import (
    activate_policy_version,
    create_policy_document,
    create_policy_version,
    get_application_policy_citations,
    ingest_policy_chunks,
    select_active_policy_version,
)
from shared.errors import AppError, request_id_from_request

router = APIRouter(prefix="/api/v1/policies", tags=["policies"])

application_citations_router = APIRouter(
    prefix="/api/v1/applications",
    tags=["policy-citations"],
)

require_policy_citations_reader = require_role(
    "loan_officer",
    "reviewer",
    "admin",
)


@router.post("", response_model=PolicyDocumentResponse, status_code=201)
async def create_policy_document_endpoint(
    body: CreatePolicyDocumentRequest,
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    admin: Annotated[UserProfile, Depends(require_admin)],
) -> PolicyDocumentResponse:
    return await create_policy_document(
        pool,
        title=body.title,
        lender_label=body.lender_label,
        created_by_user_id=admin.id,
    )


@router.get("/active", response_model=PolicyVersionWithRules)
async def get_active_policy_endpoint(
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    _user: Annotated[UserProfile, Depends(get_current_active_user)],
    as_of_date: Annotated[date, Query()],
) -> PolicyVersionWithRules:
    selected = await select_active_policy_version(pool, as_of_date=as_of_date)
    if selected is None:
        raise AppError(
            code="POLICY_NO_APPLICABLE",
            message="No active policy version covers the as-of date.",
            status_code=404,
            details={"as_of_date": as_of_date.isoformat()},
        )
    return selected


@router.post(
    "/{policy_doc_id}/versions",
    response_model=PolicyVersionResponse,
    status_code=201,
)
async def create_policy_version_endpoint(
    policy_doc_id: UUID,
    body: CreatePolicyVersionRequest,
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    admin: Annotated[UserProfile, Depends(require_admin)],
) -> PolicyVersionResponse:
    return await create_policy_version(
        pool,
        policy_document_id=policy_doc_id,
        version_label=body.version_label,
        annual_interest_rate_pct=body.annual_interest_rate_pct,
        effective_from=body.effective_from,
        effective_to=body.effective_to,
        thresholds=body.thresholds,
        doc_requirements=body.doc_requirements,
        created_by_user_id=admin.id,
    )


@router.post(
    "/versions/{version_id}/chunks",
    response_model=PolicyChunksIngestResponse,
    status_code=201,
)
async def ingest_policy_chunks_endpoint(
    version_id: UUID,
    body: IngestPolicyChunksRequest,
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    _admin: Annotated[UserProfile, Depends(require_admin)],
) -> PolicyChunksIngestResponse:
    chunks = await ingest_policy_chunks(
        pool,
        policy_version_id=version_id,
        markdown_text=body.markdown_text,
    )
    return PolicyChunksIngestResponse(policy_version_id=version_id, chunks=chunks)


@router.post(
    "/versions/{version_id}/activate",
    response_model=PolicyVersionResponse,
)
async def activate_policy_version_endpoint(
    version_id: UUID,
    request: Request,
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    admin: Annotated[UserProfile, Depends(require_admin)],
) -> PolicyVersionResponse:
    return await activate_policy_version(
        pool,
        policy_version_id=version_id,
        actor_user_id=admin.id,
        request_id=request_id_from_request(request),
    )


@application_citations_router.get(
    "/{application_id}/policy-citations",
    response_model=PolicyCitationsResponse,
)
async def get_application_policy_citations_endpoint(
    application_id: UUID,
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    _user: Annotated[UserProfile, Depends(require_policy_citations_reader)],
) -> PolicyCitationsResponse:
    """
    Lexical citations for the application's frozen policy version (ADR-016/020).

    Authorized for loan_officer, reviewer, and admin.
    """
    return await get_application_policy_citations(
        pool,
        application_id=application_id,
    )
