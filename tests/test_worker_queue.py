"""Worker queue claim / lease / fence tests against real PostgreSQL (M05-T02)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from psycopg import AsyncConnection
from psycopg.errors import RaiseException
from psycopg.rows import dict_row

from modules.worker import queue
from shared.settings import get_settings
from tests.conftest import _insert_test_user, _purge_user


async def _cleanup_processing(
    db_conn: AsyncConnection[Any],
    application_id: UUID,
) -> None:
    await db_conn.execute(
        "DELETE FROM workflow_node_events WHERE application_id = %s",
        (application_id,),
    )
    await db_conn.execute(
        "DELETE FROM workflow_checkpoints WHERE application_id = %s",
        (application_id,),
    )
    await db_conn.execute(
        "DELETE FROM processing_runs WHERE application_id = %s",
        (application_id,),
    )
    await db_conn.commit()


async def _cleanup_application(
    db_conn: AsyncConnection[Any],
    application_id: UUID,
) -> None:
    await _cleanup_processing(db_conn, application_id)
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


async def _insert_application(
    db_conn: AsyncConnection[Any],
    *,
    created_by_user_id: UUID,
    status: str = "draft",
    reference_code: str | None = None,
) -> UUID:
    now = datetime.now(timezone.utc)
    ref = reference_code or f"LL-{uuid4().hex[:12].upper()}"
    submitted_at = now if status != "draft" else None
    row = await db_conn.execute(
        """
        INSERT INTO applications (
            reference_code,
            status,
            created_by_user_id,
            applicant_type,
            requested_amount,
            currency,
            tenure_months,
            loan_purpose,
            submitted_at,
            created_at,
            updated_at
        ) VALUES (
            %s,
            %s::application_status,
            %s,
            'salaried'::applicant_type,
            %s,
            'INR',
            36,
            'home_renovation',
            %s,
            %s,
            %s
        )
        RETURNING id
        """,
        (
            ref,
            status,
            created_by_user_id,
            Decimal("500000.00"),
            submitted_at,
            now,
            now,
        ),
    )
    result = await row.fetchone()
    assert result is not None

    if status != "draft":
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
                'draft'::application_status,
                %s::application_status,
                'user'::audit_actor_type,
                %s,
                'test_seed',
                %s
            )
            """,
            (result["id"], status, created_by_user_id, now),
        )
    return result["id"]


async def _fetch_run(
    db_conn: AsyncConnection[Any],
    run_id: UUID,
) -> dict[str, Any]:
    row = await db_conn.execute(
        """
        SELECT
            id,
            application_id,
            status::text AS status,
            fence_token,
            locked_by,
            lease_expires_at,
            started_at,
            finished_at,
            error_code
          FROM processing_runs
         WHERE id = %s
        """,
        (run_id,),
    )
    result = await row.fetchone()
    assert result is not None
    return dict(result)


async def _insert_checkpoint(
    db_conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    processing_run_id: UUID,
    claim_fence: int,
    node_name: str = "placeholder",
) -> UUID:
    now = datetime.now(timezone.utc)
    row = await db_conn.execute(
        """
        INSERT INTO workflow_checkpoints (
            application_id,
            processing_run_id,
            claim_fence,
            node_name,
            sequence_no,
            status,
            state_json,
            created_at,
            updated_at
        ) VALUES (
            %s, %s, %s, %s, 1, 'succeeded', '{}'::jsonb, %s, %s
        )
        RETURNING id
        """,
        (
            application_id,
            processing_run_id,
            claim_fence,
            node_name,
            now,
            now,
        ),
    )
    result = await row.fetchone()
    assert result is not None
    return result["id"]


@pytest.mark.asyncio
async def test_enqueue_creates_queued_run(
    db_conn: AsyncConnection[Any],
) -> None:
    user = await _insert_test_user(
        db_conn,
        email="worker.queue.enqueue@example.com",
        full_name="Worker Queue Enqueue",
        role="loan_officer",
        password="test-password-not-a-secret",
    )
    application_id: UUID | None = None
    try:
        application_id = await _insert_application(
            db_conn,
            created_by_user_id=user["id"],
            reference_code=f"LL-WQ-ENQ-{uuid4().hex[:6].upper()}",
        )
        run_id = await queue.enqueue_run(
            db_conn,
            application_id,
            trigger="submit",
            idempotency_key=f"idem-{uuid4().hex}",
            provider_snapshot={"ocr": "none"},
        )
        await db_conn.commit()

        run = await _fetch_run(db_conn, run_id)
        assert run["status"] == "queued"
        assert run["fence_token"] == 0
        assert run["locked_by"] is None
        assert run["lease_expires_at"] is None
    finally:
        if application_id is not None:
            await _cleanup_application(db_conn, application_id)
        await _purge_user(db_conn, user["id"])


@pytest.mark.asyncio
async def test_claim_next_run_sets_running_and_increments_fence(
    db_conn: AsyncConnection[Any],
) -> None:
    user = await _insert_test_user(
        db_conn,
        email="worker.queue.claim@example.com",
        full_name="Worker Queue Claim",
        role="loan_officer",
        password="test-password-not-a-secret",
    )
    application_id: UUID | None = None
    try:
        application_id = await _insert_application(
            db_conn,
            created_by_user_id=user["id"],
            status="submitted",
            reference_code=f"LL-WQ-CLM-{uuid4().hex[:6].upper()}",
        )
        await db_conn.commit()

        run_id = await queue.enqueue_run(db_conn, application_id, trigger="submit")
        await db_conn.execute(
            """
            UPDATE processing_runs
               SET created_at = %s
             WHERE id = %s
            """,
            (datetime(2000, 1, 1, tzinfo=timezone.utc), run_id),
        )
        await db_conn.commit()

        claim = await queue.claim_next_run(db_conn, "worker-a", lease_seconds=60)
        await queue.transition_application_to_processing(db_conn, application_id)
        await db_conn.commit()

        assert claim is not None
        assert claim.id == run_id
        assert claim.status == "running"
        assert claim.fence_token == 1
        assert claim.claim_fence == 1
        assert claim.locked_by == "worker-a"
        assert claim.lease_expires_at is not None

        run = await _fetch_run(db_conn, run_id)
        assert run["status"] == "running"
        assert run["fence_token"] == 1

        app_row = await db_conn.execute(
            "SELECT status::text AS status FROM applications WHERE id = %s",
            (application_id,),
        )
        application = await app_row.fetchone()
        assert application is not None
        assert application["status"] == "processing"

        history = await db_conn.execute(
            """
            SELECT from_status::text AS from_status,
                   to_status::text AS to_status,
                   actor_type::text AS actor_type
              FROM application_state_history
             WHERE application_id = %s
               AND to_status = 'processing'::application_status
            """,
            (application_id,),
        )
        hist = await history.fetchone()
        assert hist is not None
        assert hist["from_status"] == "submitted"
        assert hist["actor_type"] == "system"
    finally:
        if application_id is not None:
            await _cleanup_application(db_conn, application_id)
        await _purge_user(db_conn, user["id"])


@pytest.mark.asyncio
async def test_two_concurrent_workers_do_not_claim_same_run(
    db_conn: AsyncConnection[Any],
) -> None:
    settings = get_settings()
    user = await _insert_test_user(
        db_conn,
        email="worker.queue.skip@example.com",
        full_name="Worker Queue Skip Locked",
        role="loan_officer",
        password="test-password-not-a-secret",
    )
    application_id: UUID | None = None
    try:
        application_id = await _insert_application(
            db_conn,
            created_by_user_id=user["id"],
            reference_code=f"LL-WQ-SKP-{uuid4().hex[:6].upper()}",
        )
        run_id = await queue.enqueue_run(db_conn, application_id, trigger="submit")
        # Prefer this run in ORDER BY created_at if older queued rows exist.
        await db_conn.execute(
            """
            UPDATE processing_runs
               SET created_at = %s
             WHERE id = %s
            """,
            (datetime(2000, 1, 1, tzinfo=timezone.utc), run_id),
        )
        await db_conn.commit()

        async def _claim(worker_id: str) -> UUID | None:
            async with await AsyncConnection.connect(
                settings.DATABASE_URL,
                row_factory=dict_row,
            ) as conn:
                claim = await queue.claim_next_run(conn, worker_id, lease_seconds=60)
                await conn.commit()
                return None if claim is None else claim.id

        first, second = await asyncio.gather(
            _claim("worker-concurrent-1"),
            _claim("worker-concurrent-2"),
        )
        ours = [claimed for claimed in (first, second) if claimed == run_id]
        assert len(ours) == 1, "SKIP LOCKED must allow only one claim of the same run"

        run = await _fetch_run(db_conn, run_id)
        assert run["status"] == "running"
        assert run["fence_token"] == 1
        assert run["locked_by"] in {
            "worker-concurrent-1",
            "worker-concurrent-2",
        }
    finally:
        if application_id is not None:
            await _cleanup_application(db_conn, application_id)
        await _purge_user(db_conn, user["id"])


@pytest.mark.asyncio
async def test_expired_lease_reclaimed_with_incremented_fence(
    db_conn: AsyncConnection[Any],
) -> None:
    user = await _insert_test_user(
        db_conn,
        email="worker.queue.reclaim@example.com",
        full_name="Worker Queue Reclaim",
        role="loan_officer",
        password="test-password-not-a-secret",
    )
    application_id: UUID | None = None
    try:
        application_id = await _insert_application(
            db_conn,
            created_by_user_id=user["id"],
            reference_code=f"LL-WQ-RCL-{uuid4().hex[:6].upper()}",
        )
        run_id = await queue.enqueue_run(db_conn, application_id, trigger="submit")
        await db_conn.execute(
            """
            UPDATE processing_runs
               SET created_at = %s
             WHERE id = %s
            """,
            (datetime(2000, 1, 1, tzinfo=timezone.utc), run_id),
        )
        await db_conn.commit()

        first = await queue.claim_next_run(db_conn, "worker-stale", lease_seconds=60)
        await db_conn.commit()
        assert first is not None
        assert first.id == run_id
        assert first.fence_token == 1
        stale_fence = first.claim_fence

        # Expire the lease without failing the run (ADR-015).
        past = datetime.now(timezone.utc) - timedelta(seconds=5)
        await db_conn.execute(
            """
            UPDATE processing_runs
               SET lease_expires_at = %s
             WHERE id = %s
            """,
            (past, run_id),
        )
        await db_conn.commit()

        second = await queue.claim_next_run(db_conn, "worker-fresh", lease_seconds=60)
        await db_conn.commit()
        assert second is not None
        assert second.id == run_id
        assert second.locked_by == "worker-fresh"
        assert second.fence_token == stale_fence + 1
        assert second.status == "running"

        run = await _fetch_run(db_conn, run_id)
        assert run["status"] == "running"
        assert run["error_code"] is None
        assert run["fence_token"] == stale_fence + 1
    finally:
        if application_id is not None:
            await _cleanup_application(db_conn, application_id)
        await _purge_user(db_conn, user["id"])


@pytest.mark.asyncio
async def test_stale_worker_cannot_renew_or_write_checkpoint(
    db_conn: AsyncConnection[Any],
) -> None:
    user = await _insert_test_user(
        db_conn,
        email="worker.queue.stale@example.com",
        full_name="Worker Queue Stale",
        role="loan_officer",
        password="test-password-not-a-secret",
    )
    application_id: UUID | None = None
    try:
        application_id = await _insert_application(
            db_conn,
            created_by_user_id=user["id"],
            reference_code=f"LL-WQ-STL-{uuid4().hex[:6].upper()}",
        )
        run_id = await queue.enqueue_run(db_conn, application_id, trigger="submit")
        await db_conn.execute(
            """
            UPDATE processing_runs
               SET created_at = %s
             WHERE id = %s
            """,
            (datetime(2000, 1, 1, tzinfo=timezone.utc), run_id),
        )
        await db_conn.commit()

        first = await queue.claim_next_run(db_conn, "worker-stale", lease_seconds=60)
        await db_conn.commit()
        assert first is not None
        assert first.id == run_id
        stale_fence = first.claim_fence

        past = datetime.now(timezone.utc) - timedelta(seconds=5)
        await db_conn.execute(
            """
            UPDATE processing_runs
               SET lease_expires_at = %s
             WHERE id = %s
            """,
            (past, run_id),
        )
        await db_conn.commit()

        second = await queue.claim_next_run(db_conn, "worker-fresh", lease_seconds=60)
        await db_conn.commit()
        assert second is not None
        assert second.id == run_id
        assert second.fence_token == stale_fence + 1

        renewed = await queue.renew_lease(
            db_conn,
            run_id,
            stale_fence,
            lease_seconds=60,
        )
        await db_conn.commit()
        assert renewed is False

        fresh_renewed = await queue.renew_lease(
            db_conn,
            run_id,
            second.claim_fence,
            lease_seconds=60,
        )
        await db_conn.commit()
        assert fresh_renewed is True

        with pytest.raises(RaiseException, match="STALE_WORKER_FENCE"):
            await _insert_checkpoint(
                db_conn,
                application_id=application_id,
                processing_run_id=run_id,
                claim_fence=stale_fence,
            )
            await db_conn.commit()
        await db_conn.rollback()

        checkpoint_id = await _insert_checkpoint(
            db_conn,
            application_id=application_id,
            processing_run_id=run_id,
            claim_fence=second.claim_fence,
        )
        await db_conn.commit()
        assert checkpoint_id is not None

        completed = await queue.complete_run(
            db_conn,
            run_id,
            stale_fence,
            "succeeded",
        )
        await db_conn.commit()
        assert completed is False

        run = await _fetch_run(db_conn, run_id)
        assert run["status"] == "running"
        assert run["fence_token"] == second.claim_fence
    finally:
        if application_id is not None:
            await _cleanup_application(db_conn, application_id)
        await _purge_user(db_conn, user["id"])
