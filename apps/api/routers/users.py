"""Admin user management routes (Architecture §11 Users; M02-T06)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from psycopg_pool import AsyncConnectionPool

from modules.identity.deps import get_pool
from modules.identity.rbac import require_admin
from modules.identity.schemas import (
    CreateUserRequest,
    UpdateUserRequest,
    UserListResponse,
    UserProfile,
)
from modules.identity.service import (
    create_user,
    get_user,
    list_users,
    update_user,
)
from shared.errors import request_id_from_request
from shared.settings import Settings, get_settings

router = APIRouter(prefix="/api/v1/users", tags=["users"])


@router.post("", response_model=UserProfile, status_code=201)
async def create_user_endpoint(
    body: CreateUserRequest,
    request: Request,
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    admin: Annotated[UserProfile, Depends(require_admin)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> UserProfile:
    return await create_user(
        pool,
        actor_user_id=admin.id,
        email=body.email,
        full_name=body.full_name,
        role=body.role,
        temporary_password=body.temporary_password,
        request_id=request_id_from_request(request),
        settings=settings,
    )


@router.get("", response_model=UserListResponse)
async def list_users_endpoint(
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    _admin: Annotated[UserProfile, Depends(require_admin)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> UserListResponse:
    return await list_users(pool, offset=offset, limit=limit)


@router.get("/{user_id}", response_model=UserProfile)
async def get_user_endpoint(
    user_id: UUID,
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    _admin: Annotated[UserProfile, Depends(require_admin)],
) -> UserProfile:
    return await get_user(pool, user_id=user_id)


@router.patch("/{user_id}", response_model=UserProfile)
async def update_user_endpoint(
    user_id: UUID,
    body: UpdateUserRequest,
    request: Request,
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    admin: Annotated[UserProfile, Depends(require_admin)],
) -> UserProfile:
    return await update_user(
        pool,
        actor_user_id=admin.id,
        user_id=user_id,
        role=body.role,
        is_active=body.is_active,
        request_id=request_id_from_request(request),
    )
