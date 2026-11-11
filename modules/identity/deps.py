"""FastAPI dependencies for session authentication."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from psycopg_pool import AsyncConnectionPool

from modules.identity.schemas import AuthenticatedPrincipal, UserProfile
from modules.identity.service import resolve_principal
from shared.errors import AppError
from shared.settings import Settings, get_settings


def get_pool(request: Request) -> AsyncConnectionPool:
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise AppError(
            code="DB_POOL_UNAVAILABLE",
            message="Database pool is not initialized.",
            status_code=503,
        )
    return pool


def reject_bearer_jwt(request: Request) -> None:
    """MVP HTTP API does not accept bearer JWTs (ADR-019)."""
    authorization = request.headers.get("authorization")
    if authorization is None:
        return
    scheme = authorization.split(" ", 1)[0].lower()
    if scheme == "bearer":
        raise AppError(
            code="AUTH_BEARER_REJECTED",
            message="Bearer JWT authentication is not supported.",
            status_code=401,
        )


async def get_current_principal(
    request: Request,
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthenticatedPrincipal:
    reject_bearer_jwt(request)
    cookie_name = settings.SESSION_COOKIE_NAME
    raw_secret = request.cookies.get(cookie_name)
    return await resolve_principal(
        pool,
        raw_session_secret=raw_secret,
        method=request.method,
        csrf_header=request.headers.get("x-csrf-token"),
        settings=settings,
    )


async def get_current_active_user(
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
) -> UserProfile:
    """Route protection: live unrevoked session and active user (CSRF already checked)."""
    return principal.user
