"""Audit hash-chain integrity verifier and admin endpoint (M15-T01)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from psycopg import AsyncConnection

from apps.api.middleware.security import SECURITY_HEADERS
from modules.governance.audit import append_audit_event
from modules.governance.verifier import (
    GENESIS_EVENT_HASH,
    verify_audit_log_integrity,
)


async def _resync_chain_state(db_conn: AsyncConnection[Any]) -> None:
    await db_conn.execute("SELECT set_config('loanlens.allow_audit_append', 'on', true)")
    await db_conn.execute(
        """
        UPDATE audit_chain_state AS s
        SET last_chain_seq = COALESCE(e.max_seq, 0),
            last_event_hash = COALESCE(
                e.last_hash,
                %s
            )
        FROM (
            SELECT MAX(chain_seq) AS max_seq,
                   (ARRAY_AGG(event_hash ORDER BY chain_seq DESC))[1] AS last_hash
            FROM audit_events
        ) AS e
        WHERE s.id = 1
        """,
        (GENESIS_EVENT_HASH,),
    )
    await db_conn.commit()


async def _append_sample_events(
    db_conn: AsyncConnection[Any],
    *,
    actor_user_id: UUID,
    count: int = 3,
) -> list[UUID]:
    ids: list[UUID] = []
    for index in range(count):
        event_id = await append_audit_event(
            db_conn,
            action=f"test.audit.event.{index}",
            actor_type="user",
            actor_user_id=actor_user_id,
            entity_type="test",
            entity_id=actor_user_id,
            after_json={"index": index, "marker": "audit-verifier"},
            request_id=f"audit-verifier-{index}",
        )
        ids.append(event_id)
    await db_conn.commit()
    return ids


@pytest.mark.asyncio
async def test_verify_accepts_intact_chain(
    db_conn: AsyncConnection[Any],
    admin_user: dict[str, Any],
) -> None:
    event_ids = await _append_sample_events(db_conn, actor_user_id=admin_user["id"])
    try:
        report = await verify_audit_log_integrity(db_conn)
        assert report.is_valid is True
        assert report.tampered_event_id is None
        assert report.error_message is None
        assert report.total_events_checked >= len(event_ids)
    finally:
        await db_conn.execute(
            "SELECT set_config('loanlens.allow_audit_append', 'on', true)"
        )
        await db_conn.execute(
            "DELETE FROM audit_events WHERE id = ANY(%s)",
            (event_ids,),
        )
        await _resync_chain_state(db_conn)


@pytest.mark.asyncio
async def test_verify_detects_planted_sequence_gap(
    db_conn: AsyncConnection[Any],
    admin_user: dict[str, Any],
) -> None:
    event_ids = await _append_sample_events(
        db_conn, actor_user_id=admin_user["id"], count=3
    )
    try:
        middle_id = event_ids[1]
        await db_conn.execute(
            "SELECT set_config('loanlens.allow_audit_append', 'on', true)"
        )
        await db_conn.execute(
            "DELETE FROM audit_events WHERE id = %s",
            (middle_id,),
        )
        # Leave singleton pointing at the old head so the gap is the failure mode.
        await db_conn.commit()

        report = await verify_audit_log_integrity(db_conn)
        assert report.is_valid is False
        assert report.error_message is not None
        assert "chain_seq" in report.error_message or "gap" in report.error_message.lower()
        assert report.tampered_event_id is not None
    finally:
        await db_conn.execute(
            "SELECT set_config('loanlens.allow_audit_append', 'on', true)"
        )
        await db_conn.execute(
            "DELETE FROM audit_events WHERE id = ANY(%s)",
            (event_ids,),
        )
        await _resync_chain_state(db_conn)


@pytest.mark.asyncio
async def test_verify_detects_tampered_event_hash(
    db_conn: AsyncConnection[Any],
    admin_user: dict[str, Any],
) -> None:
    event_ids = await _append_sample_events(
        db_conn, actor_user_id=admin_user["id"], count=2
    )
    try:
        target_id = event_ids[0]
        await db_conn.execute(
            "SELECT set_config('loanlens.allow_audit_append', 'on', true)"
        )
        await db_conn.execute(
            """
            UPDATE audit_events
               SET event_hash = %s
             WHERE id = %s
            """,
            ("f" * 64, target_id),
        )
        await db_conn.commit()

        report = await verify_audit_log_integrity(db_conn)
        assert report.is_valid is False
        assert report.tampered_event_id == target_id
        assert report.error_message is not None
        assert "tampered" in report.error_message.lower()
    finally:
        await db_conn.execute(
            "SELECT set_config('loanlens.allow_audit_append', 'on', true)"
        )
        await db_conn.execute(
            "DELETE FROM audit_events WHERE id = ANY(%s)",
            (event_ids,),
        )
        await _resync_chain_state(db_conn)


@pytest.mark.asyncio
async def test_verify_detects_tampered_payload(
    db_conn: AsyncConnection[Any],
    admin_user: dict[str, Any],
) -> None:
    event_ids = await _append_sample_events(
        db_conn, actor_user_id=admin_user["id"], count=2
    )
    try:
        target_id = event_ids[-1]
        await db_conn.execute(
            "SELECT set_config('loanlens.allow_audit_append', 'on', true)"
        )
        await db_conn.execute(
            """
            UPDATE audit_events
               SET after_json = '{"mutated": true}'::jsonb
             WHERE id = %s
            """,
            (target_id,),
        )
        await db_conn.commit()

        report = await verify_audit_log_integrity(db_conn)
        assert report.is_valid is False
        assert report.tampered_event_id == target_id
        assert report.error_message is not None
        assert "tampered" in report.error_message.lower()
    finally:
        await db_conn.execute(
            "SELECT set_config('loanlens.allow_audit_append', 'on', true)"
        )
        await db_conn.execute(
            "DELETE FROM audit_events WHERE id = ANY(%s)",
            (event_ids,),
        )
        await _resync_chain_state(db_conn)


@pytest.mark.asyncio
async def test_admin_verify_endpoint_and_security_headers(
    client: AsyncClient,
    admin_user: dict[str, Any],
    test_user: dict[str, Any],
) -> None:
    denied = await client.get("/api/v1/audit/verify")
    assert denied.status_code == 401

    officer_login = await client.post(
        "/api/v1/auth/login",
        json={"email": test_user["email"], "password": test_user["password"]},
    )
    assert officer_login.status_code == 200, officer_login.text
    forbidden = await client.get("/api/v1/audit/verify")
    assert forbidden.status_code == 403
    await client.post(
        "/api/v1/auth/logout",
        headers={"X-CSRF-Token": officer_login.json()["csrf_token"]},
    )

    admin_login = await client.post(
        "/api/v1/auth/login",
        json={"email": admin_user["email"], "password": admin_user["password"]},
    )
    assert admin_login.status_code == 200, admin_login.text
    try:
        response = await client.get("/api/v1/audit/verify")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["is_valid"] is True
        assert body["tampered_event_id"] is None
        assert body["error_message"] is None
        assert isinstance(body["total_events_checked"], int)

        for header, value in SECURITY_HEADERS.items():
            assert response.headers.get(header) == value

        metrics = await client.get("/metrics")
        assert metrics.status_code == 200
        assert "loanlens_applications_status_total" in metrics.text
        assert "loanlens_processing_runs_duration_seconds" in metrics.text
        assert "loanlens_verification_findings_total" in metrics.text
        assert "loanlens_assessments_total" in metrics.text
        for header, value in SECURITY_HEADERS.items():
            assert metrics.headers.get(header) == value
    finally:
        await client.post(
            "/api/v1/auth/logout",
            headers={"X-CSRF-Token": admin_login.json()["csrf_token"]},
        )
