"""API tests for application withdraw and archive (M03-T03)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from psycopg import AsyncConnection

from apps.api.main import app

_ARCHIVE_PERMITTED_STATUSES = (
    "draft",
    "processing_failed",
    "clarification_required",
    "awaiting_human_review",
    "review_completed",
)

_STATUS_PATHS: dict[str, list[tuple[str, str]]] = {
    "draft": [],
    "processing_failed": [
        ("draft", "submitted"),
        ("submitted", "processing"),
        ("processing", "processing_failed"),
    ],
    "clarification_required": [
        ("draft", "submitted"),
        ("submitted", "processing"),
        ("processing", "awaiting_human_review"),
        ("awaiting_human_review", "in_review"),
        ("in_review", "clarification_required"),
    ],
    "awaiting_human_review": [
        ("draft", "submitted"),
        ("submitted", "processing"),
        ("processing", "awaiting_human_review"),
    ],
    "review_completed": [
        ("draft", "submitted"),
        ("submitted", "processing"),
        ("processing", "awaiting_human_review"),
        ("awaiting_human_review", "in_review"),
        ("in_review", "review_completed"),
    ],
    "submitted": [
        ("draft", "submitted"),
    ],
}


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


async def _cleanup_application(
    db_conn: AsyncConnection[Any],
    application_id: UUID,
) -> None:
    await db_conn.execute(
        "SELECT set_config('loanlens.allow_audit_append', 'on', true)"
    )
    await db_conn.execute(
        "DELETE FROM audit_events WHERE application_id = %s",
        (application_id,),
    )
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
    await db_conn.execute(
        "ALTER TABLE application_state_history "
        "DISABLE TRIGGER application_state_history_append_only_trg"
    )
    await db_conn.execute(
        "DELETE FROM application_state_history WHERE application_id = %s",
        (application_id,),
    )
    await db_conn.execute(
        "ALTER TABLE application_state_history "
        "ENABLE TRIGGER application_state_history_append_only_trg"
    )
    await db_conn.execute(
        "DELETE FROM applicants WHERE application_id = %s",
        (application_id,),
    )
    await db_conn.execute(
        "DELETE FROM applications WHERE id = %s",
        (application_id,),
    )
    await db_conn.commit()


def _valid_create_payload() -> dict[str, Any]:
    return {
        "applicant_type": "salaried",
        "requested_amount": "500000.00",
        "tenure_months": 36,
        "loan_purpose": "home_renovation",
        "applicant": {
            "full_name": "Lifecycle Applicant",
            "date_of_birth": "1990-01-15",
            "pan": "ABCDE1234F",
            "current_address": "12 Example Road, Bhubaneswar",
            "employer_or_business_name": "Example Employer Pvt Ltd",
            "time_in_job_or_business_months": 24,
            "gross_monthly_income": "100000.00",
            "net_monthly_income": "80000.00",
            "total_existing_monthly_emis": "5000.00",
        },
    }


async def _create_draft(
    client: AsyncClient,
    *,
    csrf: str,
) -> UUID:
    response = await client.post(
        "/api/v1/applications",
        headers={"X-CSRF-Token": csrf},
        json=_valid_create_payload(),
    )
    assert response.status_code == 201, response.text
    return UUID(response.json()["id"])


async def _force_status(
    db_conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    actor_user_id: UUID,
    target_status: str,
) -> None:
    path = _STATUS_PATHS[target_status]
    now = datetime.now(timezone.utc)
    for from_status, to_status in path:
        await db_conn.execute(
            """
            INSERT INTO application_state_history (
                application_id,
                from_status,
                to_status,
                actor_type,
                actor_user_id,
                reason,
                created_at
            ) VALUES (
                %s,
                %s::application_status,
                %s::application_status,
                'user'::audit_actor_type,
                %s,
                %s,
                %s
            )
            """,
            (
                application_id,
                from_status,
                to_status,
                actor_user_id,
                f"test force {from_status}->{to_status}",
                now,
            ),
        )
        await db_conn.execute(
            """
            UPDATE applications
               SET status = %s::application_status,
                   updated_at = %s
             WHERE id = %s
            """,
            (to_status, now, application_id),
        )
    await db_conn.commit()


@pytest.mark.asyncio
async def test_loan_officer_withdraws_draft_with_reason(
    client: AsyncClient,
    test_user: dict[str, Any],
    db_conn: AsyncConnection[Any],
) -> None:
    csrf = await _login(
        client,
        email=test_user["email"],
        password=test_user["password"],
    )
    application_id: UUID | None = None
    try:
        application_id = await _create_draft(client, csrf=csrf)
        response = await client.post(
            f"/api/v1/applications/{application_id}/withdraw",
            headers={"X-CSRF-Token": csrf},
            json={"reason": "Applicant cancelled request"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "archived"
        assert body["withdraw_reason"] == "Applicant cancelled request"
        assert body["archived_at"] is not None

        history = await db_conn.execute(
            """
            SELECT from_status::text AS from_status,
                   to_status::text AS to_status,
                   actor_type::text AS actor_type,
                   actor_user_id,
                   reason
              FROM application_state_history
             WHERE application_id = %s
               AND to_status = 'archived'::application_status
            """,
            (application_id,),
        )
        rows = await history.fetchall()
        assert len(rows) == 1
        assert rows[0]["from_status"] == "draft"
        assert rows[0]["to_status"] == "archived"
        assert rows[0]["actor_type"] == "user"
        assert rows[0]["actor_user_id"] == test_user["id"]
        assert rows[0]["reason"] == "Applicant cancelled request"

        audit = await db_conn.execute(
            """
            SELECT action, actor_user_id, rationale, entity_type, entity_id
              FROM audit_events
             WHERE application_id = %s
               AND action = 'application.withdraw'
            """,
            (application_id,),
        )
        events = await audit.fetchall()
        assert len(events) == 1
        assert events[0]["actor_user_id"] == test_user["id"]
        assert events[0]["rationale"] == "Applicant cancelled request"
        assert events[0]["entity_type"] == "application"
        assert events[0]["entity_id"] == application_id
    finally:
        if application_id is not None:
            await _cleanup_application(db_conn, application_id)


@pytest.mark.asyncio
async def test_withdraw_rejects_empty_reason(
    client: AsyncClient,
    test_user: dict[str, Any],
    db_conn: AsyncConnection[Any],
) -> None:
    csrf = await _login(
        client,
        email=test_user["email"],
        password=test_user["password"],
    )
    application_id: UUID | None = None
    try:
        application_id = await _create_draft(client, csrf=csrf)
        for payload in ({"reason": ""}, {"reason": "   "}):
            response = await client.post(
                f"/api/v1/applications/{application_id}/withdraw",
                headers={"X-CSRF-Token": csrf},
                json=payload,
            )
            assert response.status_code == 422, response.text
    finally:
        if application_id is not None:
            await _cleanup_application(db_conn, application_id)


@pytest.mark.asyncio
async def test_reviewer_forbidden_on_withdraw_and_archive(
    client: AsyncClient,
    reviewer_user: dict[str, Any],
    test_user: dict[str, Any],
    db_conn: AsyncConnection[Any],
) -> None:
    officer_client = AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    )
    application_id: UUID | None = None
    try:
        async with officer_client:
            officer_csrf = await _login(
                officer_client,
                email=test_user["email"],
                password=test_user["password"],
            )
            application_id = await _create_draft(officer_client, csrf=officer_csrf)

        reviewer_csrf = await _login(
            client,
            email=reviewer_user["email"],
            password=reviewer_user["password"],
        )
        withdraw = await client.post(
            f"/api/v1/applications/{application_id}/withdraw",
            headers={"X-CSRF-Token": reviewer_csrf},
            json={"reason": "Should not withdraw"},
        )
        assert withdraw.status_code == 403
        assert withdraw.json()["code"] == "AUTH_FORBIDDEN"

        archive = await client.post(
            f"/api/v1/applications/{application_id}/archive",
            headers={"X-CSRF-Token": reviewer_csrf},
            json={"reason": "Should not archive"},
        )
        assert archive.status_code == 403
        assert archive.json()["code"] == "AUTH_FORBIDDEN"
    finally:
        if application_id is not None:
            await _cleanup_application(db_conn, application_id)


@pytest.mark.asyncio
async def test_loan_officer_forbidden_on_archive(
    client: AsyncClient,
    test_user: dict[str, Any],
    db_conn: AsyncConnection[Any],
) -> None:
    csrf = await _login(
        client,
        email=test_user["email"],
        password=test_user["password"],
    )
    application_id: UUID | None = None
    try:
        application_id = await _create_draft(client, csrf=csrf)
        response = await client.post(
            f"/api/v1/applications/{application_id}/archive",
            headers={"X-CSRF-Token": csrf},
            json={"reason": "Officer cannot archive"},
        )
        assert response.status_code == 403
        assert response.json()["code"] == "AUTH_FORBIDDEN"
    finally:
        if application_id is not None:
            await _cleanup_application(db_conn, application_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", _ARCHIVE_PERMITTED_STATUSES)
async def test_admin_archives_from_permitted_states(
    client: AsyncClient,
    admin_user: dict[str, Any],
    test_user: dict[str, Any],
    db_conn: AsyncConnection[Any],
    status: str,
) -> None:
    officer_client = AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    )
    application_id: UUID | None = None
    try:
        async with officer_client:
            officer_csrf = await _login(
                officer_client,
                email=test_user["email"],
                password=test_user["password"],
            )
            application_id = await _create_draft(officer_client, csrf=officer_csrf)

        await _force_status(
            db_conn,
            application_id=application_id,
            actor_user_id=test_user["id"],
            target_status=status,
        )

        admin_csrf = await _login(
            client,
            email=admin_user["email"],
            password=admin_user["password"],
        )
        reason = f"Admin archive from {status}"
        response = await client.post(
            f"/api/v1/applications/{application_id}/archive",
            headers={"X-CSRF-Token": admin_csrf},
            json={"reason": reason},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "archived"
        assert body["withdraw_reason"] == reason
        assert body["archived_at"] is not None

        history = await db_conn.execute(
            """
            SELECT from_status::text AS from_status,
                   to_status::text AS to_status,
                   reason
              FROM application_state_history
             WHERE application_id = %s
               AND to_status = 'archived'::application_status
            """,
            (application_id,),
        )
        rows = await history.fetchall()
        assert len(rows) == 1
        assert rows[0]["from_status"] == status
        assert rows[0]["reason"] == reason

        audit = await db_conn.execute(
            """
            SELECT action, actor_user_id, rationale
              FROM audit_events
             WHERE application_id = %s
               AND action = 'application.archive'
            """,
            (application_id,),
        )
        events = await audit.fetchall()
        assert len(events) == 1
        assert events[0]["actor_user_id"] == admin_user["id"]
        assert events[0]["rationale"] == reason
    finally:
        if application_id is not None:
            await _cleanup_application(db_conn, application_id)


@pytest.mark.asyncio
async def test_withdraw_and_archive_fail_when_already_archived(
    client: AsyncClient,
    test_user: dict[str, Any],
    admin_user: dict[str, Any],
    db_conn: AsyncConnection[Any],
) -> None:
    csrf = await _login(
        client,
        email=test_user["email"],
        password=test_user["password"],
    )
    application_id: UUID | None = None
    try:
        application_id = await _create_draft(client, csrf=csrf)
        first = await client.post(
            f"/api/v1/applications/{application_id}/withdraw",
            headers={"X-CSRF-Token": csrf},
            json={"reason": "Initial withdrawal"},
        )
        assert first.status_code == 200, first.text

        second_withdraw = await client.post(
            f"/api/v1/applications/{application_id}/withdraw",
            headers={"X-CSRF-Token": csrf},
            json={"reason": "Already archived"},
        )
        assert second_withdraw.status_code == 409
        assert second_withdraw.json()["code"] == "APPLICATION_ALREADY_ARCHIVED"

        admin_csrf = await _login(
            client,
            email=admin_user["email"],
            password=admin_user["password"],
        )
        second_archive = await client.post(
            f"/api/v1/applications/{application_id}/archive",
            headers={"X-CSRF-Token": admin_csrf},
            json={"reason": "Already archived"},
        )
        assert second_archive.status_code == 409
        assert second_archive.json()["code"] == "APPLICATION_ALREADY_ARCHIVED"
    finally:
        if application_id is not None:
            await _cleanup_application(db_conn, application_id)


@pytest.mark.asyncio
async def test_withdraw_rejects_unpermitted_status(
    client: AsyncClient,
    test_user: dict[str, Any],
    db_conn: AsyncConnection[Any],
) -> None:
    csrf = await _login(
        client,
        email=test_user["email"],
        password=test_user["password"],
    )
    application_id: UUID | None = None
    try:
        application_id = await _create_draft(client, csrf=csrf)
        await _force_status(
            db_conn,
            application_id=application_id,
            actor_user_id=test_user["id"],
            target_status="submitted",
        )
        response = await client.post(
            f"/api/v1/applications/{application_id}/withdraw",
            headers={"X-CSRF-Token": csrf},
            json={"reason": "Cannot withdraw submitted"},
        )
        assert response.status_code == 409
        assert response.json()["code"] == "APPLICATION_INVALID_STATE"
    finally:
        if application_id is not None:
            await _cleanup_application(db_conn, application_id)
