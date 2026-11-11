"""Checkpoint persistence and resumable workflow runner tests (M05-T03)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from psycopg import AsyncConnection
from psycopg.errors import RaiseException

from modules.worker import checkpoints, queue
from modules.worker.runner import (
    PLACEHOLDER_PIPELINE,
    StaleWorkerFenceError,
    run_workflow,
)
from modules.worker.schemas import RunClaim
from shared.db import create_pool
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
    await db_conn.execute("SELECT set_config('loanlens.allow_audit_append', 'on', true)")
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


async def _insert_application(
    db_conn: AsyncConnection[Any],
    *,
    created_by_user_id: UUID,
    status: str = "submitted",
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


async def _claim_prepared(
    db_conn: AsyncConnection[Any],
    application_id: UUID,
    worker_id: str,
) -> RunClaim:
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

    claim = await queue.claim_next_run(db_conn, worker_id, lease_seconds=120)
    assert claim is not None
    assert claim.id == run_id
    await queue.transition_application_to_processing(db_conn, application_id)
    await db_conn.commit()
    return claim


async def _fetch_run(
    db_conn: AsyncConnection[Any],
    run_id: UUID,
) -> dict[str, Any]:
    row = await db_conn.execute(
        """
        SELECT
            id,
            status::text AS status,
            fence_token,
            locked_by,
            lease_expires_at,
            error_code,
            finished_at
          FROM processing_runs
         WHERE id = %s
        """,
        (run_id,),
    )
    result = await row.fetchone()
    assert result is not None
    return dict(result)


async def _fetch_app_status(
    db_conn: AsyncConnection[Any],
    application_id: UUID,
) -> str:
    row = await db_conn.execute(
        "SELECT status::text AS status FROM applications WHERE id = %s",
        (application_id,),
    )
    result = await row.fetchone()
    assert result is not None
    return str(result["status"])


async def _checkpoint_rows(
    db_conn: AsyncConnection[Any],
    run_id: UUID,
) -> list[dict[str, Any]]:
    row = await db_conn.execute(
        """
        SELECT node_name, sequence_no, status, state_json, error_code
          FROM workflow_checkpoints
         WHERE processing_run_id = %s
         ORDER BY sequence_no
        """,
        (run_id,),
    )
    return [dict(item) for item in await row.fetchall()]


async def _node_event_statuses(
    db_conn: AsyncConnection[Any],
    run_id: UUID,
) -> list[tuple[str, str]]:
    row = await db_conn.execute(
        """
        SELECT node_name, status
          FROM workflow_node_events
         WHERE processing_run_id = %s
         ORDER BY created_at, id
        """,
        (run_id,),
    )
    return [(str(item["node_name"]), str(item["status"])) for item in await row.fetchall()]


@pytest.mark.asyncio
async def test_linear_workflow_succeeds_and_awaits_human_review(
    db_conn: AsyncConnection[Any],
) -> None:
    settings = get_settings()
    user = await _insert_test_user(
        db_conn,
        email="worker.runner.success@example.com",
        full_name="Worker Runner Success",
        role="loan_officer",
        password="test-password-not-a-secret",
    )
    application_id: UUID | None = None
    pool = create_pool(settings.DATABASE_URL)
    await pool.open()
    try:
        application_id = await _insert_application(
            db_conn,
            created_by_user_id=user["id"],
            reference_code=f"LL-WR-OK-{uuid4().hex[:6].upper()}",
        )
        await db_conn.commit()
        claim = await _claim_prepared(db_conn, application_id, "worker-runner-ok")

        executed: list[str] = []

        async def _track(name: str):
            async def _handler(state: dict[str, Any]) -> dict[str, Any]:
                executed.append(name)
                updated = dict(state)
                updated[name] = True
                return updated

            return _handler

        steps = [
            ("classify", await _track("classify")),
            ("extract", await _track("extract")),
            ("verify", await _track("verify")),
            ("evaluate", await _track("evaluate")),
        ]

        terminal = await run_workflow(pool, claim, steps, request_id=str(uuid4()))
        assert terminal == "succeeded"
        assert executed == ["classify", "extract", "verify", "evaluate"]

        run = await _fetch_run(db_conn, claim.id)
        assert run["status"] == "succeeded"
        assert run["locked_by"] is None
        assert run["lease_expires_at"] is None
        assert run["finished_at"] is not None

        assert await _fetch_app_status(db_conn, application_id) == "awaiting_human_review"

        cps = await _checkpoint_rows(db_conn, claim.id)
        assert [c["node_name"] for c in cps] == [
            "classify",
            "extract",
            "verify",
            "evaluate",
        ]
        assert all(c["status"] == "succeeded" for c in cps)
        assert cps[-1]["state_json"]["evaluate"] is True

        events = await _node_event_statuses(db_conn, claim.id)
        assert ("classify", "started") in events
        assert ("evaluate", "completed") in events
    finally:
        await pool.close()
        if application_id is not None:
            await _cleanup_application(db_conn, application_id)
        await _purge_user(db_conn, user["id"])


@pytest.mark.asyncio
async def test_workflow_failure_marks_run_and_application_failed(
    db_conn: AsyncConnection[Any],
) -> None:
    settings = get_settings()
    user = await _insert_test_user(
        db_conn,
        email="worker.runner.fail@example.com",
        full_name="Worker Runner Fail",
        role="loan_officer",
        password="test-password-not-a-secret",
    )
    application_id: UUID | None = None
    pool = create_pool(settings.DATABASE_URL)
    await pool.open()
    try:
        application_id = await _insert_application(
            db_conn,
            created_by_user_id=user["id"],
            reference_code=f"LL-WR-FL-{uuid4().hex[:6].upper()}",
        )
        await db_conn.commit()
        claim = await _claim_prepared(db_conn, application_id, "worker-runner-fail")

        executed: list[str] = []

        async def ok(state: dict[str, Any]) -> dict[str, Any]:
            executed.append("classify")
            return {**state, "classify": True}

        async def boom(state: dict[str, Any]) -> dict[str, Any]:
            executed.append("extract")
            raise RuntimeError("extract exploded")

        async def should_not_run(state: dict[str, Any]) -> dict[str, Any]:
            executed.append("verify")
            return state

        steps = [
            ("classify", ok),
            ("extract", boom),
            ("verify", should_not_run),
            ("evaluate", should_not_run),
        ]

        terminal = await run_workflow(pool, claim, steps, request_id=str(uuid4()))
        assert terminal == "failed"
        assert executed == ["classify", "extract"]

        run = await _fetch_run(db_conn, claim.id)
        assert run["status"] == "failed"
        assert run["error_code"] == "RuntimeError"
        assert run["locked_by"] is None

        assert await _fetch_app_status(db_conn, application_id) == "processing_failed"

        cps = await _checkpoint_rows(db_conn, claim.id)
        assert len(cps) == 2
        assert cps[0]["node_name"] == "classify"
        assert cps[0]["status"] == "succeeded"
        assert cps[1]["node_name"] == "extract"
        assert cps[1]["status"] == "failed"
        assert cps[1]["error_code"] == "RuntimeError"

        events = await _node_event_statuses(db_conn, claim.id)
        assert ("extract", "failed") in events
        assert ("verify", "started") not in events

        audit = await db_conn.execute(
            """
            SELECT action
              FROM audit_events
             WHERE application_id = %s
               AND action = 'processing.failed'
            """,
            (application_id,),
        )
        audit_row = await audit.fetchone()
        assert audit_row is not None
    finally:
        await pool.close()
        if application_id is not None:
            await _cleanup_application(db_conn, application_id)
        await _purge_user(db_conn, user["id"])


@pytest.mark.asyncio
async def test_mid_workflow_resumption_skips_completed_nodes(
    db_conn: AsyncConnection[Any],
) -> None:
    settings = get_settings()
    user = await _insert_test_user(
        db_conn,
        email="worker.runner.resume@example.com",
        full_name="Worker Runner Resume",
        role="loan_officer",
        password="test-password-not-a-secret",
    )
    application_id: UUID | None = None
    pool = create_pool(settings.DATABASE_URL)
    await pool.open()
    try:
        application_id = await _insert_application(
            db_conn,
            created_by_user_id=user["id"],
            reference_code=f"LL-WR-RS-{uuid4().hex[:6].upper()}",
        )
        await db_conn.commit()
        claim = await _claim_prepared(db_conn, application_id, "worker-runner-resume")

        # Simulate a pause after node 2 by writing succeeded checkpoints.
        await checkpoints.save_checkpoint(
            db_conn,
            application_id,
            claim.id,
            claim.claim_fence,
            "classify",
            1,
            "succeeded",
            {"classify": True},
        )
        await checkpoints.save_checkpoint(
            db_conn,
            application_id,
            claim.id,
            claim.claim_fence,
            "extract",
            2,
            "succeeded",
            {"classify": True, "extract": True},
        )
        await checkpoints.record_node_event(
            db_conn,
            application_id,
            claim.id,
            "classify",
            "completed",
        )
        await checkpoints.record_node_event(
            db_conn,
            application_id,
            claim.id,
            "extract",
            "completed",
        )
        await db_conn.commit()

        executed: list[str] = []

        async def track(name: str):
            async def _handler(state: dict[str, Any]) -> dict[str, Any]:
                executed.append(name)
                return {**state, name: True}

            return _handler

        steps = [
            ("classify", await track("classify")),
            ("extract", await track("extract")),
            ("verify", await track("verify")),
            ("evaluate", await track("evaluate")),
        ]

        terminal = await run_workflow(pool, claim, steps, request_id=str(uuid4()))
        assert terminal == "succeeded"
        assert executed == ["verify", "evaluate"], (
            "resumption must skip already succeeded nodes"
        )

        cps = await _checkpoint_rows(db_conn, claim.id)
        assert [c["node_name"] for c in cps] == [
            "classify",
            "extract",
            "verify",
            "evaluate",
        ]
        assert cps[2]["sequence_no"] == 3
        assert cps[3]["sequence_no"] == 4
        assert cps[3]["state_json"]["classify"] is True
        assert cps[3]["state_json"]["extract"] is True
        assert cps[3]["state_json"]["verify"] is True
        assert cps[3]["state_json"]["evaluate"] is True

        run = await _fetch_run(db_conn, claim.id)
        assert run["status"] == "succeeded"
        assert await _fetch_app_status(db_conn, application_id) == "awaiting_human_review"
    finally:
        await pool.close()
        if application_id is not None:
            await _cleanup_application(db_conn, application_id)
        await _purge_user(db_conn, user["id"])


@pytest.mark.asyncio
async def test_stale_worker_fence_halts_execution(
    db_conn: AsyncConnection[Any],
) -> None:
    settings = get_settings()
    user = await _insert_test_user(
        db_conn,
        email="worker.runner.stale@example.com",
        full_name="Worker Runner Stale",
        role="loan_officer",
        password="test-password-not-a-secret",
    )
    application_id: UUID | None = None
    pool = create_pool(settings.DATABASE_URL)
    await pool.open()
    try:
        application_id = await _insert_application(
            db_conn,
            created_by_user_id=user["id"],
            reference_code=f"LL-WR-ST-{uuid4().hex[:6].upper()}",
        )
        await db_conn.commit()
        stale_claim = await _claim_prepared(
            db_conn,
            application_id,
            "worker-runner-stale",
        )
        stale_fence = stale_claim.claim_fence

        # Expire lease and reclaim with a fresh fence (ADR-015).
        past = datetime.now(timezone.utc) - timedelta(seconds=5)
        await db_conn.execute(
            """
            UPDATE processing_runs
               SET lease_expires_at = %s
             WHERE id = %s
            """,
            (past, stale_claim.id),
        )
        await db_conn.commit()

        fresh = await queue.claim_next_run(
            db_conn,
            "worker-runner-fresh",
            lease_seconds=120,
        )
        await db_conn.commit()
        assert fresh is not None
        assert fresh.id == stale_claim.id
        assert fresh.claim_fence == stale_fence + 1

        executed: list[str] = []

        async def mark(state: dict[str, Any]) -> dict[str, Any]:
            executed.append("classify")
            return {**state, "classify": True}

        with pytest.raises(StaleWorkerFenceError, match="STALE_WORKER_FENCE"):
            await run_workflow(
                pool,
                stale_claim,
                [("classify", mark), ("extract", mark)],
                request_id=str(uuid4()),
            )

        assert executed == ["classify"]

        run = await _fetch_run(db_conn, stale_claim.id)
        assert run["status"] == "running"
        assert run["fence_token"] == fresh.claim_fence
        assert run["locked_by"] == "worker-runner-fresh"

        cps = await _checkpoint_rows(db_conn, stale_claim.id)
        assert cps == []

        assert await _fetch_app_status(db_conn, application_id) == "processing"
    finally:
        await pool.close()
        if application_id is not None:
            await _cleanup_application(db_conn, application_id)
        await _purge_user(db_conn, user["id"])


@pytest.mark.asyncio
async def test_save_checkpoint_requires_matching_fence(
    db_conn: AsyncConnection[Any],
) -> None:
    user = await _insert_test_user(
        db_conn,
        email="worker.runner.fenceunit@example.com",
        full_name="Worker Runner Fence Unit",
        role="loan_officer",
        password="test-password-not-a-secret",
    )
    application_id: UUID | None = None
    try:
        application_id = await _insert_application(
            db_conn,
            created_by_user_id=user["id"],
            reference_code=f"LL-WR-FU-{uuid4().hex[:6].upper()}",
        )
        await db_conn.commit()
        claim = await _claim_prepared(db_conn, application_id, "worker-fence-unit")

        with pytest.raises(RaiseException, match="STALE_WORKER_FENCE"):
            await checkpoints.save_checkpoint(
                db_conn,
                application_id,
                claim.id,
                claim.claim_fence + 99,
                "classify",
                1,
                "succeeded",
                {},
            )
            await db_conn.commit()
        await db_conn.rollback()

        checkpoint_id = await checkpoints.save_checkpoint(
            db_conn,
            application_id,
            claim.id,
            claim.claim_fence,
            "classify",
            1,
            "succeeded",
            {"ok": True},
        )
        await db_conn.commit()
        assert checkpoint_id is not None

        latest = await checkpoints.get_latest_checkpoint(db_conn, claim.id)
        assert latest is not None
        assert latest.node_name == "classify"
        assert latest.sequence_no == 1
        assert latest.state_json["ok"] is True
    finally:
        if application_id is not None:
            await _cleanup_application(db_conn, application_id)
        await _purge_user(db_conn, user["id"])


@pytest.mark.asyncio
async def test_placeholder_pipeline_shape() -> None:
    assert [name for name, _ in PLACEHOLDER_PIPELINE] == [
        "classify",
        "extract",
        "verify",
        "calculate",
        "policy_retrieval",
        "evaluate",
    ]
