"""Shared fixtures for API tests against the running PostgreSQL database."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from psycopg import AsyncConnection
from psycopg.rows import dict_row

from apps.api.main import app
from modules.identity.passwords import hash_password
from modules.identity.tokens import hash_session_secret
from shared.settings import get_settings

# psycopg async requires SelectorEventLoop on Windows (not ProactorEventLoop).
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest.fixture(scope="session")
def event_loop_policy() -> asyncio.AbstractEventLoopPolicy:
    if sys.platform == "win32":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.DefaultEventLoopPolicy()


@pytest.fixture(scope="session")
def settings():
    get_settings.cache_clear()
    return get_settings()


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


@pytest_asyncio.fixture
async def db_conn(settings) -> AsyncIterator[AsyncConnection[Any]]:
    async with await AsyncConnection.connect(
        settings.DATABASE_URL,
        row_factory=dict_row,
    ) as conn:
        yield conn


async def _purge_user(db_conn: AsyncConnection[Any], user_id: UUID) -> None:
    """Remove sessions, audit rows referencing the user, then the user (test DB only)."""
    await db_conn.execute(
        "DELETE FROM user_sessions WHERE user_id = %s",
        (user_id,),
    )
    await db_conn.execute("SELECT set_config('loanlens.allow_audit_append', 'on', true)")
    await db_conn.execute(
        """
        DELETE FROM audit_events
        WHERE actor_user_id = %s OR entity_id = %s
        """,
        (user_id, user_id),
    )
    # Re-sync chain singleton after test deletes (append-only path not used here).
    await db_conn.execute(
        """
        UPDATE audit_chain_state AS s
        SET last_chain_seq = COALESCE(e.max_seq, 0),
            last_event_hash = COALESCE(
                e.last_hash,
                '0000000000000000000000000000000000000000000000000000000000000000'
            )
        FROM (
            SELECT MAX(chain_seq) AS max_seq,
                   (ARRAY_AGG(event_hash ORDER BY chain_seq DESC))[1] AS last_hash
            FROM audit_events
        ) AS e
        WHERE s.id = 1
        """
    )
    await db_conn.execute("DELETE FROM users WHERE id = %s", (user_id,))
    await db_conn.commit()


async def _insert_test_user(
    db_conn: AsyncConnection[Any],
    *,
    email: str,
    full_name: str,
    role: str,
    password: str,
) -> dict[str, Any]:
    password_hash = hash_password(password)
    now = datetime.now(timezone.utc)

    existing = await db_conn.execute(
        "SELECT id FROM users WHERE email = %s",
        (email,),
    )
    prior = await existing.fetchone()
    if prior is not None:
        await _purge_user(db_conn, prior["id"])

    row = await db_conn.execute(
        """
        INSERT INTO users (email, full_name, role, password_hash, is_active, created_at, updated_at)
        VALUES (%s, %s, %s::user_role, %s, true, %s, %s)
        RETURNING id, email, full_name, role::text AS role, is_active
        """,
        (email, full_name, role, password_hash, now, now),
    )
    user = await row.fetchone()
    assert user is not None
    await db_conn.commit()
    return {**user, "password": password}


@pytest_asyncio.fixture
async def test_user(db_conn: AsyncConnection[Any]) -> AsyncIterator[dict[str, Any]]:
    """Insert a synthetic active loan officer; delete on teardown."""
    payload = await _insert_test_user(
        db_conn,
        email="auth.officer@example.com",
        full_name="Auth Test Officer",
        role="loan_officer",
        password="test-password-not-a-secret",
    )
    try:
        yield payload
    finally:
        await _purge_user(db_conn, payload["id"])


@pytest_asyncio.fixture
async def admin_user(db_conn: AsyncConnection[Any]) -> AsyncIterator[dict[str, Any]]:
    payload = await _insert_test_user(
        db_conn,
        email="auth.admin@example.com",
        full_name="Auth Test Admin",
        role="admin",
        password="admin-password-not-a-secret",
    )
    try:
        yield payload
    finally:
        await _purge_user(db_conn, payload["id"])


@pytest_asyncio.fixture
async def reviewer_user(db_conn: AsyncConnection[Any]) -> AsyncIterator[dict[str, Any]]:
    payload = await _insert_test_user(
        db_conn,
        email="auth.reviewer@example.com",
        full_name="Auth Test Reviewer",
        role="reviewer",
        password="reviewer-password-not-a-secret",
    )
    try:
        yield payload
    finally:
        await _purge_user(db_conn, payload["id"])


async def revoke_session_cookie(
    db_conn: AsyncConnection[Any],
    *,
    raw_secret: str,
) -> UUID:
    token_hash = hash_session_secret(raw_secret)
    now = datetime.now(timezone.utc)
    row = await db_conn.execute(
        """
        UPDATE user_sessions
        SET revoked_at = %s
        WHERE token_hash = %s
        RETURNING id
        """,
        (now, token_hash),
    )
    result = await row.fetchone()
    assert result is not None
    await db_conn.commit()
    return result["id"]
