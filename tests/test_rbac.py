"""API tests for Admin RBAC and /api/v1/users (M02-T06)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from psycopg import AsyncConnection

from apps.api.main import app
from modules.identity.tokens import hash_session_secret
from shared.settings import get_settings
from tests.conftest import _purge_user


async def _login(
    client: AsyncClient,
    *,
    email: str,
    password: str,
) -> str:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


@pytest.mark.asyncio
async def test_admin_can_create_list_and_update_users(
    client: AsyncClient,
    admin_user: dict[str, Any],
    db_conn: AsyncConnection[Any],
) -> None:
    csrf = await _login(
        client,
        email=admin_user["email"],
        password=admin_user["password"],
    )
    created_ids: list[UUID] = []

    try:
        create = await client.post(
            "/api/v1/users",
            headers={"X-CSRF-Token": csrf},
            json={
                "email": "  New.Officer@Example.com ",
                "full_name": "New Officer",
                "role": "loan_officer",
                "temporary_password": "temp-password-not-a-secret",
            },
        )
        assert create.status_code == 201, create.text
        body = create.json()
        assert body["email"] == "new.officer@example.com"
        assert body["role"] == "loan_officer"
        assert body["is_active"] is True
        created_ids.append(UUID(body["id"]))

        listing = await client.get("/api/v1/users", params={"offset": 0, "limit": 50})
        assert listing.status_code == 200
        list_body = listing.json()
        assert list_body["total"] >= 1
        assert any(item["id"] == body["id"] for item in list_body["items"])

        detail = await client.get(f"/api/v1/users/{body['id']}")
        assert detail.status_code == 200
        assert detail.json()["email"] == "new.officer@example.com"

        update = await client.patch(
            f"/api/v1/users/{body['id']}",
            headers={"X-CSRF-Token": csrf},
            json={"role": "reviewer"},
        )
        assert update.status_code == 200, update.text
        assert update.json()["role"] == "reviewer"

        audit = await db_conn.execute(
            """
            SELECT action FROM audit_events
            WHERE entity_id = %s AND action IN ('user.create', 'user.update_role')
            ORDER BY chain_seq ASC
            """,
            (body["id"],),
        )
        actions = [row["action"] for row in await audit.fetchall()]
        assert actions == ["user.create", "user.update_role"]
    finally:
        for user_id in created_ids:
            await _purge_user(db_conn, user_id)


@pytest.mark.asyncio
async def test_deactivating_user_revokes_active_sessions(
    client: AsyncClient,
    admin_user: dict[str, Any],
    db_conn: AsyncConnection[Any],
) -> None:
    settings = get_settings()
    admin_csrf = await _login(
        client,
        email=admin_user["email"],
        password=admin_user["password"],
    )

    create = await client.post(
        "/api/v1/users",
        headers={"X-CSRF-Token": admin_csrf},
        json={
            "email": "deactivate.target@example.com",
            "full_name": "Deactivate Target",
            "role": "loan_officer",
            "temporary_password": "target-password-not-a-secret",
        },
    )
    assert create.status_code == 201, create.text
    target = create.json()
    target_id = UUID(target["id"])

    try:
            # Separate cookie jar so the admin session is not overwritten.
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as target_client:
            target_login = await target_client.post(
                "/api/v1/auth/login",
                json={
                    "email": "deactivate.target@example.com",
                    "password": "target-password-not-a-secret",
                },
            )
            assert target_login.status_code == 200
            raw_secret = target_login.cookies[settings.SESSION_COOKIE_NAME]
            token_hash = hash_session_secret(raw_secret)

            me_ok = await target_client.get("/api/v1/auth/me")
            assert me_ok.status_code == 200

            deactivate = await client.patch(
                f"/api/v1/users/{target_id}",
                headers={"X-CSRF-Token": admin_csrf},
                json={"is_active": False},
            )
            assert deactivate.status_code == 200, deactivate.text
            assert deactivate.json()["is_active"] is False

            session_row = await db_conn.execute(
                "SELECT revoked_at FROM user_sessions WHERE token_hash = %s",
                (token_hash,),
            )
            session = await session_row.fetchone()
            assert session is not None
            assert session["revoked_at"] is not None

            me_revoked = await target_client.get("/api/v1/auth/me")
            assert me_revoked.status_code == 401
            assert me_revoked.json()["code"] in {
                "AUTH_SESSION_REVOKED",
                "AUTH_USER_INACTIVE",
            }

            audit = await db_conn.execute(
                """
                SELECT action FROM audit_events
                WHERE entity_id = %s AND action = 'user.deactivate'
                """,
                (target_id,),
            )
            rows = await audit.fetchall()
            assert len(rows) == 1
    finally:
        await _purge_user(db_conn, target_id)


@pytest.mark.asyncio
async def test_loan_officer_forbidden_on_users(
    client: AsyncClient,
    test_user: dict[str, Any],
) -> None:
    csrf = await _login(
        client,
        email=test_user["email"],
        password=test_user["password"],
    )
    response = await client.get("/api/v1/users")
    assert response.status_code == 403
    assert response.json()["code"] == "AUTH_FORBIDDEN"

    create = await client.post(
        "/api/v1/users",
        headers={"X-CSRF-Token": csrf},
        json={
            "email": "should.fail@example.com",
            "full_name": "Should Fail",
            "role": "loan_officer",
            "temporary_password": "x",
        },
    )
    assert create.status_code == 403
    assert create.json()["code"] == "AUTH_FORBIDDEN"


@pytest.mark.asyncio
async def test_reviewer_forbidden_on_users(
    client: AsyncClient,
    reviewer_user: dict[str, Any],
) -> None:
    await _login(
        client,
        email=reviewer_user["email"],
        password=reviewer_user["password"],
    )
    response = await client.get("/api/v1/users")
    assert response.status_code == 403
    assert response.json()["code"] == "AUTH_FORBIDDEN"


@pytest.mark.asyncio
async def test_unauthenticated_users_returns_401(client: AsyncClient) -> None:
    response = await client.get("/api/v1/users")
    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_UNAUTHENTICATED"


@pytest.mark.asyncio
async def test_create_and_deactivate_write_audit_events(
    client: AsyncClient,
    admin_user: dict[str, Any],
    db_conn: AsyncConnection[Any],
) -> None:
    csrf = await _login(
        client,
        email=admin_user["email"],
        password=admin_user["password"],
    )
    create = await client.post(
        "/api/v1/users",
        headers={"X-CSRF-Token": csrf},
        json={
            "email": "audit.subject@example.com",
            "full_name": "Audit Subject",
            "role": "reviewer",
            "temporary_password": "audit-password-not-a-secret",
        },
    )
    assert create.status_code == 201, create.text
    user_id = UUID(create.json()["id"])

    try:
        deactivate = await client.patch(
            f"/api/v1/users/{user_id}",
            headers={"X-CSRF-Token": csrf},
            json={"is_active": False},
        )
        assert deactivate.status_code == 200

        rows = await db_conn.execute(
            """
            SELECT action, actor_user_id, entity_type, entity_id, after_json
            FROM audit_events
            WHERE entity_id = %s
              AND action IN ('user.create', 'user.deactivate')
            ORDER BY chain_seq ASC
            """,
            (user_id,),
        )
        events = await rows.fetchall()
        assert [e["action"] for e in events] == ["user.create", "user.deactivate"]
        for event in events:
            assert event["actor_user_id"] == admin_user["id"]
            assert event["entity_type"] == "user"
            assert event["entity_id"] == user_id
            assert event["after_json"] is not None
    finally:
        await _purge_user(db_conn, user_id)


@pytest.mark.asyncio
async def test_invalid_role_rejected(
    client: AsyncClient,
    admin_user: dict[str, Any],
) -> None:
    csrf = await _login(
        client,
        email=admin_user["email"],
        password=admin_user["password"],
    )
    response = await client.post(
        "/api/v1/users",
        headers={"X-CSRF-Token": csrf},
        json={
            "email": "bad.role@example.com",
            "full_name": "Bad Role",
            "role": "operations_analyst",
            "temporary_password": "x",
        },
    )
    assert response.status_code == 422
