"""Auth routes: login, logout, me (Architecture §11, ADR-019)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from psycopg_pool import AsyncConnectionPool

from modules.identity.deps import (
    get_current_active_user,
    get_current_principal,
    get_pool,
    reject_bearer_jwt,
)
from modules.identity.schemas import AuthenticatedPrincipal, LoginRequest, LoginResponse, UserProfile
from modules.identity.service import login, logout
from shared.settings import Settings, get_settings

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _set_session_cookie(
    response: Response,
    *,
    raw_secret: str,
    settings: Settings,
) -> None:
    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=raw_secret,
        httponly=True,
        samesite="lax",
        secure=settings.APP_ENV != "local",
        max_age=settings.SESSION_ABSOLUTE_TIMEOUT_SECONDS,
        path="/",
    )


def _clear_session_cookie(response: Response, *, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.SESSION_COOKIE_NAME,
        path="/",
        samesite="lax",
        secure=settings.APP_ENV != "local",
        httponly=True,
    )


@router.post("/login", response_model=LoginResponse)
async def auth_login(
    body: LoginRequest,
    request: Request,
    response: Response,
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> LoginResponse:
    reject_bearer_jwt(request)
    prior = request.cookies.get(settings.SESSION_COOKIE_NAME)
    result, raw_secret = await login(
        pool,
        email=body.email,
        password=body.password,
        prior_session_secret=prior,
        settings=settings,
    )
    _set_session_cookie(response, raw_secret=raw_secret, settings=settings)
    return result


@router.post("/logout", status_code=204)
async def auth_logout(
    response: Response,
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    await logout(pool, raw_session_secret=principal.raw_session_secret)
    _clear_session_cookie(response, settings=settings)
    return Response(status_code=204)


@router.get("/me", response_model=UserProfile)
async def auth_me(
    user: Annotated[UserProfile, Depends(get_current_active_user)],
) -> UserProfile:
    return user
