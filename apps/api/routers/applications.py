"""Draft application intake routes (Architecture §11; M03-T02 / M03-T03)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from psycopg_pool import AsyncConnectionPool

from modules.identity.deps import get_current_active_user, get_pool
from modules.identity.rbac import require_admin, require_role
from modules.identity.schemas import UserProfile
from modules.intake.schemas import (
    ApplicationListResponse,
    ApplicationResponse,
    ApplicationStatus,
    ArchiveRequest,
    CreateApplicationRequest,
    UpdateApplicationRequest,
    WithdrawRequest,
)
from modules.intake.service import (
    archive_application,
    create_draft_application,
    get_application,
    list_applications,
    submit_application,
    update_draft_application,
    withdraw_application,
)
from shared.errors import request_id_from_request

router = APIRouter(prefix="/api/v1/applications", tags=["applications"])

require_loan_officer_or_admin = require_role("loan_officer", "admin")


@router.post("", response_model=ApplicationResponse, status_code=201)
async def create_application_endpoint(
    body: CreateApplicationRequest,
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    actor: Annotated[UserProfile, Depends(require_loan_officer_or_admin)],
) -> ApplicationResponse:
    return await create_draft_application(
        pool,
        actor_user_id=actor.id,
        payload=body,
    )


@router.get("", response_model=ApplicationListResponse)
async def list_applications_endpoint(
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    _user: Annotated[UserProfile, Depends(get_current_active_user)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    status: Annotated[ApplicationStatus | None, Query()] = None,
) -> ApplicationListResponse:
    return await list_applications(
        pool,
        offset=offset,
        limit=limit,
        status=status,
    )


@router.get("/{application_id}", response_model=ApplicationResponse)
async def get_application_endpoint(
    application_id: UUID,
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    _user: Annotated[UserProfile, Depends(get_current_active_user)],
) -> ApplicationResponse:
    return await get_application(pool, application_id=application_id)


@router.patch("/{application_id}", response_model=ApplicationResponse)
async def update_application_endpoint(
    application_id: UUID,
    body: UpdateApplicationRequest,
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    _actor: Annotated[UserProfile, Depends(require_loan_officer_or_admin)],
) -> ApplicationResponse:
    return await update_draft_application(
        pool,
        application_id=application_id,
        payload=body,
    )


@router.post("/{application_id}/submit", response_model=ApplicationResponse)
async def submit_application_endpoint(
    application_id: UUID,
    request: Request,
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    actor: Annotated[UserProfile, Depends(require_loan_officer_or_admin)],
) -> ApplicationResponse:
    return await submit_application(
        pool,
        application_id=application_id,
        actor_user_id=actor.id,
        actor_role=actor.role,
        request_id=request_id_from_request(request),
    )


@router.post("/{application_id}/withdraw", response_model=ApplicationResponse)
async def withdraw_application_endpoint(
    application_id: UUID,
    body: WithdrawRequest,
    request: Request,
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    actor: Annotated[UserProfile, Depends(require_loan_officer_or_admin)],
) -> ApplicationResponse:
    return await withdraw_application(
        pool,
        application_id=application_id,
        actor_user_id=actor.id,
        actor_role=actor.role,
        payload=body,
        request_id=request_id_from_request(request),
    )


@router.post("/{application_id}/archive", response_model=ApplicationResponse)
async def archive_application_endpoint(
    application_id: UUID,
    body: ArchiveRequest,
    request: Request,
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    actor: Annotated[UserProfile, Depends(require_admin)],
) -> ApplicationResponse:
    return await archive_application(
        pool,
        application_id=application_id,
        actor_user_id=actor.id,
        payload=body,
        request_id=request_id_from_request(request),
    )
