"""API tests for server-side session authentication (M02-T05 / ADR-019)."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from psycopg import AsyncConnection

from modules.identity.tokens import hash_session_secret
from shared.settings import get_settings
from tests.conftest import revoke_session_cookie


@pytest.mark.asyncio
async def test_login_success_sets_httponly_cookie(
    client: AsyncClient,
    test_user: dict[str, Any],
    db_conn: AsyncConnection[Any],
) -> None:
    settings = get_settings()
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": test_user["email"], "password": test_user["password"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert "csrf_token" in body
    assert body["user"]["email"] == test_user["email"]
    assert body["user"]["role"] == "loan_officer"

    assert settings.SESSION_COOKIE_NAME in response.cookies
    raw_secret = response.cookies[settings.SESSION_COOKIE_NAME]
    set_cookie = response.headers.get("set-cookie", "")
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie or "SameSite=Lax" in set_cookie

    token_hash = hash_session_secret(raw_secret)
    row = await db_conn.execute(
        "SELECT token_hash, revoked_at FROM user_sessions WHERE token_hash = %s",
        (token_hash,),
    )
    session = await row.fetchone()
    assert session is not None
    assert session["token_hash"] == token_hash
    assert session["revoked_at"] is None
    # Raw cookie secret must never equal the stored digest column value.
    assert raw_secret != session["token_hash"]


@pytest.mark.asyncio
async def test_login_rejects_invalid_credentials(
    client: AsyncClient,
    test_user: dict[str, Any],
) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": test_user["email"], "password": "wrong-password"},
    )
    assert response.status_code == 401
    body = response.json()
    assert body["code"] == "AUTH_INVALID_CREDENTIALS"
    assert "request_id" in body


@pytest.mark.asyncio
async def test_revoked_session_is_rejected_immediately(
    client: AsyncClient,
    test_user: dict[str, Any],
    db_conn: AsyncConnection[Any],
) -> None:
    settings = get_settings()
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": test_user["email"], "password": test_user["password"]},
    )
    assert login.status_code == 200
    raw_secret = login.cookies[settings.SESSION_COOKIE_NAME]

    await revoke_session_cookie(db_conn, raw_secret=raw_secret)

    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 401
    assert me.json()["code"] == "AUTH_SESSION_REVOKED"


@pytest.mark.asyncio
async def test_mutating_request_without_csrf_is_rejected(
    client: AsyncClient,
    test_user: dict[str, Any],
) -> None:
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": test_user["email"], "password": test_user["password"]},
    )
    assert login.status_code == 200

    logout = await client.post("/api/v1/auth/logout")
    assert logout.status_code == 403
    assert logout.json()["code"] == "AUTH_CSRF_MISSING"


@pytest.mark.asyncio
async def test_logout_with_csrf_revokes_and_clears_cookie(
    client: AsyncClient,
    test_user: dict[str, Any],
    db_conn: AsyncConnection[Any],
) -> None:
    settings = get_settings()
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": test_user["email"], "password": test_user["password"]},
    )
    assert login.status_code == 200
    csrf = login.json()["csrf_token"]
    raw_secret = login.cookies[settings.SESSION_COOKIE_NAME]
    token_hash = hash_session_secret(raw_secret)

    logout = await client.post(
        "/api/v1/auth/logout",
        headers={"X-CSRF-Token": csrf},
    )
    assert logout.status_code == 204

    row = await db_conn.execute(
        "SELECT revoked_at FROM user_sessions WHERE token_hash = %s",
        (token_hash,),
    )
    session = await row.fetchone()
    assert session is not None
    assert session["revoked_at"] is not None

    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 401


@pytest.mark.asyncio
async def test_bearer_jwt_is_rejected(
    client: AsyncClient,
    test_user: dict[str, Any],
) -> None:
    response = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer eyJhbGciOiJub25lIn0.fake.payload"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_BEARER_REJECTED"


@pytest.mark.asyncio
async def test_me_returns_profile_when_authenticated(
    client: AsyncClient,
    test_user: dict[str, Any],
) -> None:
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": test_user["email"], "password": test_user["password"]},
    )
    assert login.status_code == 200

    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == test_user["email"]
    assert body["full_name"] == "Auth Test Officer"
    assert body["is_active"] is True
