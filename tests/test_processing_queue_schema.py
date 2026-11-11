"""Schema tests for processing_runs, checkpoints, and fence triggers (M05-T01)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from psycopg import AsyncConnection
from psycopg.errors import CheckViolation, RaiseException, UniqueViolation

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


async def _insert_draft_application(
    db_conn: AsyncConnection[Any],
    *,
    created_by_user_id: UUID,
    reference_code: str | None = None,
) -> UUID:
    now = datetime.now(timezone.utc)
    ref = reference_code or f"LL-{uuid4().hex[:12].upper()}"
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
            created_at,
            updated_at
        ) VALUES (
            %s,
            'draft'::application_status,
            %s,
            'salaried'::applicant_type,
            %s,
            'INR',
            36,
            'home_renovation',
            %s,
            %s
        )
        RETURNING id
        """,
        (ref, created_by_user_id, Decimal("500000.00"), now, now),
    )
    result = await row.fetchone()
    assert result is not None
    return result["id"]


async def _insert_processing_run(
    db_conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    status: str = "queued",
    attempt_number: int = 1,
    trigger: str = "submit",
    idempotency_key: str | None = None,
    fence_token: int = 0,
    error_code: str | None = None,
    finished_at: datetime | None = None,
) -> UUID:
    now = datetime.now(timezone.utc)
    row = await db_conn.execute(
        """
        INSERT INTO processing_runs (
            application_id,
            input_snapshot_id,
            status,
            attempt_number,
            trigger,
            idempotency_key,
            fence_token,
            error_code,
            finished_at,
            created_at
        ) VALUES (
            %s,
            NULL,
            %s::processing_run_status,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s
        )
        RETURNING id
        """,
        (
            application_id,
            status,
            attempt_number,
            trigger,
            idempotency_key,
            fence_token,
            error_code,
            finished_at,
            now,
        ),
    )
    result = await row.fetchone()
    assert result is not None
    return result["id"]


async def _insert_checkpoint(
    db_conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    processing_run_id: UUID,
    claim_fence: int,
    node_name: str = "placeholder",
    sequence_no: int = 1,
    status: str = "succeeded",
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
            %s, %s, %s, %s, %s, %s, '{}'::jsonb, %s, %s
        )
        RETURNING id
        """,
        (
            application_id,
            processing_run_id,
            claim_fence,
            node_name,
            sequence_no,
            status,
            now,
            now,
        ),
    )
    result = await row.fetchone()
    assert result is not None
    return result["id"]


@pytest.mark.asyncio
async def test_queued_run_insert_and_single_active_run_constraint(
    db_conn: AsyncConnection[Any],
) -> None:
    user = await _insert_test_user(
        db_conn,
        email="queue.schema.active@example.com",
        full_name="Queue Schema Active",
        role="loan_officer",
        password="test-password-not-a-secret",
    )
    application_id: UUID | None = None
    try:
        application_id = await _insert_draft_application(
            db_conn,
            created_by_user_id=user["id"],
            reference_code=f"LL-PR-ACT-{uuid4().hex[:6].upper()}",
        )
        run_id = await _insert_processing_run(
            db_conn,
            application_id=application_id,
            status="queued",
        )
        await db_conn.commit()

        row = await db_conn.execute(
            """
            SELECT id, status::text AS status, fence_token, attempt_number
              FROM processing_runs
             WHERE id = %s
            """,
            (run_id,),
        )
        run = await row.fetchone()
        assert run is not None
        assert run["status"] == "queued"
        assert run["fence_token"] == 0
        assert run["attempt_number"] == 1

        active_idx = await db_conn.execute(
            """
            SELECT indexname
              FROM pg_indexes
             WHERE schemaname = 'public'
               AND indexname = 'uq_processing_runs_active'
            """
        )
        assert await active_idx.fetchone() is not None

        with pytest.raises(UniqueViolation):
            await _insert_processing_run(
                db_conn,
                application_id=application_id,
                status="running",
                fence_token=1,
                attempt_number=2,
                trigger="retry",
            )
            await db_conn.commit()
        await db_conn.rollback()

        count = await db_conn.execute(
            """
            SELECT COUNT(*)::int AS n
              FROM processing_runs
             WHERE application_id = %s
               AND status IN ('queued', 'running')
            """,
            (application_id,),
        )
        n = await count.fetchone()
        assert n is not None
        assert n["n"] == 1
    finally:
        if application_id is not None:
            await _cleanup_application(db_conn, application_id)
        await _purge_user(db_conn, user["id"])


@pytest.mark.asyncio
async def test_idempotency_key_unique_per_application(
    db_conn: AsyncConnection[Any],
) -> None:
    user = await _insert_test_user(
        db_conn,
        email="queue.schema.idem@example.com",
        full_name="Queue Schema Idem",
        role="loan_officer",
        password="test-password-not-a-secret",
    )
    application_a: UUID | None = None
    application_b: UUID | None = None
    try:
        application_a = await _insert_draft_application(
            db_conn,
            created_by_user_id=user["id"],
            reference_code=f"LL-PR-IDA-{uuid4().hex[:6].upper()}",
        )
        application_b = await _insert_draft_application(
            db_conn,
            created_by_user_id=user["id"],
            reference_code=f"LL-PR-IDB-{uuid4().hex[:6].upper()}",
        )
        key = f"idem-{uuid4().hex}"
        await _insert_processing_run(
            db_conn,
            application_id=application_a,
            status="succeeded",
            idempotency_key=key,
            finished_at=datetime.now(timezone.utc),
        )
        await db_conn.commit()

        with pytest.raises(UniqueViolation):
            await _insert_processing_run(
                db_conn,
                application_id=application_a,
                status="queued",
                idempotency_key=key,
                attempt_number=2,
                trigger="retry",
            )
            await db_conn.commit()
        await db_conn.rollback()

        # Same key on a different application is allowed.
        other_run = await _insert_processing_run(
            db_conn,
            application_id=application_b,
            status="queued",
            idempotency_key=key,
        )
        await db_conn.commit()
        assert other_run is not None

        idem_idx = await db_conn.execute(
            """
            SELECT indexname
              FROM pg_indexes
             WHERE schemaname = 'public'
               AND indexname = 'uq_processing_runs_idempotency'
            """
        )
        assert await idem_idx.fetchone() is not None
    finally:
        if application_a is not None:
            await _cleanup_application(db_conn, application_a)
        if application_b is not None:
            await _cleanup_application(db_conn, application_b)
        await _purge_user(db_conn, user["id"])


@pytest.mark.asyncio
async def test_fence_trigger_blocks_mismatched_claim_fence(
    db_conn: AsyncConnection[Any],
) -> None:
    user = await _insert_test_user(
        db_conn,
        email="queue.schema.fence@example.com",
        full_name="Queue Schema Fence",
        role="loan_officer",
        password="test-password-not-a-secret",
    )
    application_id: UUID | None = None
    try:
        application_id = await _insert_draft_application(
            db_conn,
            created_by_user_id=user["id"],
            reference_code=f"LL-PR-FN-{uuid4().hex[:6].upper()}",
        )
        run_id = await _insert_processing_run(
            db_conn,
            application_id=application_id,
            status="running",
            fence_token=3,
        )
        await db_conn.commit()

        fence_trg = await db_conn.execute(
            """
            SELECT t.tgname
              FROM pg_trigger AS t
              JOIN pg_class AS c ON c.oid = t.tgrelid
             WHERE c.relname = 'workflow_checkpoints'
               AND t.tgname = 'workflow_checkpoints_fence_enforcement_trg'
               AND NOT t.tgisinternal
            """
        )
        assert await fence_trg.fetchone() is not None

        with pytest.raises(RaiseException, match="STALE_WORKER_FENCE"):
            await _insert_checkpoint(
                db_conn,
                application_id=application_id,
                processing_run_id=run_id,
                claim_fence=2,
            )
            await db_conn.commit()
        await db_conn.rollback()

        # Matching fence is accepted.
        checkpoint_id = await _insert_checkpoint(
            db_conn,
            application_id=application_id,
            processing_run_id=run_id,
            claim_fence=3,
        )
        await db_conn.commit()
        assert checkpoint_id is not None
    finally:
        if application_id is not None:
            await _cleanup_application(db_conn, application_id)
        await _purge_user(db_conn, user["id"])


@pytest.mark.asyncio
async def test_checkpoint_update_rejected_when_run_finished(
    db_conn: AsyncConnection[Any],
) -> None:
    user = await _insert_test_user(
        db_conn,
        email="queue.schema.freeze@example.com",
        full_name="Queue Schema Freeze",
        role="loan_officer",
        password="test-password-not-a-secret",
    )
    application_id: UUID | None = None
    try:
        application_id = await _insert_draft_application(
            db_conn,
            created_by_user_id=user["id"],
            reference_code=f"LL-PR-FZ-{uuid4().hex[:6].upper()}",
        )
        run_id = await _insert_processing_run(
            db_conn,
            application_id=application_id,
            status="running",
            fence_token=1,
        )
        checkpoint_id = await _insert_checkpoint(
            db_conn,
            application_id=application_id,
            processing_run_id=run_id,
            claim_fence=1,
            status="succeeded",
        )
        await db_conn.commit()

        freeze_trg = await db_conn.execute(
            """
            SELECT t.tgname
              FROM pg_trigger AS t
              JOIN pg_class AS c ON c.oid = t.tgrelid
             WHERE c.relname = 'workflow_checkpoints'
               AND t.tgname = 'workflow_checkpoints_freeze_trg'
               AND NOT t.tgisinternal
            """
        )
        assert await freeze_trg.fetchone() is not None

        now = datetime.now(timezone.utc)
        await db_conn.execute(
            """
            UPDATE processing_runs
               SET status = 'succeeded'::processing_run_status,
                   finished_at = %s
             WHERE id = %s
            """,
            (now, run_id),
        )
        await db_conn.commit()

        with pytest.raises(CheckViolation):
            await db_conn.execute(
                """
                UPDATE workflow_checkpoints
                   SET status = 'failed',
                       updated_at = %s
                 WHERE id = %s
                """,
                (now, checkpoint_id),
            )
            await db_conn.commit()
        await db_conn.rollback()

        row = await db_conn.execute(
            """
            SELECT status
              FROM workflow_checkpoints
             WHERE id = %s
            """,
            (checkpoint_id,),
        )
        checkpoint = await row.fetchone()
        assert checkpoint is not None
        assert checkpoint["status"] == "succeeded"
    finally:
        if application_id is not None:
            await _cleanup_application(db_conn, application_id)
        await _purge_user(db_conn, user["id"])
