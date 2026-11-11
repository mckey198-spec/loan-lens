"""PostgreSQL SKIP LOCKED claim / lease / complete helpers (ADR-006, ADR-015)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from apps.api.metrics import record_application_status
from modules.worker.schemas import RunClaim, TerminalRunStatus


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def enqueue_run(
    connection: AsyncConnection[Any],
    application_id: UUID,
    trigger: str,
    idempotency_key: str | None = None,
    provider_snapshot: dict[str, Any] | None = None,
    input_snapshot_id: UUID | None = None,
) -> UUID:
    """Insert a new queued processing run; return existing id on idempotent replay."""
    snapshot = provider_snapshot if provider_snapshot is not None else {}

    if idempotency_key is not None:
        existing = await connection.execute(
            """
            SELECT id
              FROM processing_runs
             WHERE application_id = %s
               AND idempotency_key = %s
            """,
            (application_id, idempotency_key),
        )
        prior = await existing.fetchone()
        if prior is not None:
            return prior["id"]

    attempt_row = await connection.execute(
        """
        SELECT COALESCE(MAX(attempt_number), 0)::int AS max_attempt
          FROM processing_runs
         WHERE application_id = %s
        """,
        (application_id,),
    )
    attempt = await attempt_row.fetchone()
    assert attempt is not None
    attempt_number = int(attempt["max_attempt"]) + 1

    now = _utcnow()
    row = await connection.execute(
        """
        INSERT INTO processing_runs (
            application_id,
            input_snapshot_id,
            status,
            attempt_number,
            trigger,
            idempotency_key,
            fence_token,
            provider_snapshot_json,
            created_at
        ) VALUES (
            %s,
            %s,
            'queued'::processing_run_status,
            %s,
            %s,
            %s,
            0,
            %s,
            %s
        )
        RETURNING id
        """,
        (
            application_id,
            input_snapshot_id,
            attempt_number,
            trigger,
            idempotency_key,
            Jsonb(snapshot),
            now,
        ),
    )
    result = await row.fetchone()
    assert result is not None
    return result["id"]


async def claim_next_run(
    connection: AsyncConnection[Any],
    worker_id: str,
    lease_seconds: int,
) -> RunClaim | None:
    """Atomically claim the next queued or expired-lease run via SKIP LOCKED."""
    if lease_seconds < 1:
        raise ValueError("lease_seconds must be >= 1")

    row = await connection.execute(
        """
        WITH candidate AS (
            SELECT id
              FROM processing_runs
             WHERE status = 'queued'::processing_run_status
                OR (
                    status = 'running'::processing_run_status
                    AND lease_expires_at IS NOT NULL
                    AND lease_expires_at < now()
                )
             ORDER BY created_at
             FOR UPDATE SKIP LOCKED
             LIMIT 1
        )
        UPDATE processing_runs AS pr
           SET fence_token = pr.fence_token + 1,
               status = 'running'::processing_run_status,
               locked_by = %s,
               locked_at = now(),
               lease_expires_at = now() + make_interval(secs => %s),
               started_at = COALESCE(pr.started_at, now())
          FROM candidate
         WHERE pr.id = candidate.id
        RETURNING
            pr.id,
            pr.application_id,
            pr.input_snapshot_id,
            pr.status::text AS status,
            pr.attempt_number,
            pr.trigger,
            pr.idempotency_key,
            pr.fence_token,
            pr.locked_by,
            pr.locked_at,
            pr.lease_expires_at,
            pr.started_at,
            pr.provider_snapshot_json,
            pr.created_at
        """,
        (worker_id, lease_seconds),
    )
    claimed = await row.fetchone()
    if claimed is None:
        return None
    return RunClaim.model_validate(dict(claimed))


async def renew_lease(
    connection: AsyncConnection[Any],
    run_id: UUID,
    claim_fence: int,
    lease_seconds: int,
) -> bool:
    """Extend lease_expires_at only when fence_token still matches claim_fence."""
    if lease_seconds < 1:
        raise ValueError("lease_seconds must be >= 1")

    row = await connection.execute(
        """
        UPDATE processing_runs
           SET lease_expires_at = now() + make_interval(secs => %s)
         WHERE id = %s
           AND fence_token = %s
           AND status = 'running'::processing_run_status
        RETURNING id
        """,
        (lease_seconds, run_id, claim_fence),
    )
    renewed = await row.fetchone()
    return renewed is not None


async def complete_run(
    connection: AsyncConnection[Any],
    run_id: UUID,
    claim_fence: int,
    status: TerminalRunStatus,
    error_code: str | None = None,
    error_message: str | None = None,
) -> bool:
    """Mark a running claim succeeded or failed and clear lease locks."""
    if status not in ("succeeded", "failed"):
        raise ValueError("status must be 'succeeded' or 'failed'")
    if status == "failed" and (error_code is None or not error_code.strip()):
        raise ValueError("error_code is required when status is 'failed'")

    row = await connection.execute(
        """
        UPDATE processing_runs
           SET status = %s::processing_run_status,
               finished_at = now(),
               locked_by = NULL,
               locked_at = NULL,
               lease_expires_at = NULL,
               error_code = %s,
               error_message = %s
         WHERE id = %s
           AND fence_token = %s
           AND status = 'running'::processing_run_status
        RETURNING id
        """,
        (
            status,
            error_code if status == "failed" else None,
            error_message if status == "failed" else None,
            run_id,
            claim_fence,
        ),
    )
    completed = await row.fetchone()
    return completed is not None


async def release_lease(
    connection: AsyncConnection[Any],
    run_id: UUID,
    claim_fence: int,
) -> bool:
    """Expire the lease without failing the run so another worker may reclaim."""
    row = await connection.execute(
        """
        UPDATE processing_runs
           SET lease_expires_at = now() - interval '1 second'
         WHERE id = %s
           AND fence_token = %s
           AND status = 'running'::processing_run_status
        RETURNING id
        """,
        (run_id, claim_fence),
    )
    released = await row.fetchone()
    return released is not None


async def transition_application_to_processing(
    connection: AsyncConnection[Any],
    application_id: UUID,
    *,
    reason: str = "worker_claim",
) -> bool:
    """Move application submitted → processing with state history; no-op if already processing."""
    locked = await connection.execute(
        """
        SELECT id, status::text AS status
          FROM applications
         WHERE id = %s
         FOR UPDATE
        """,
        (application_id,),
    )
    application = await locked.fetchone()
    if application is None:
        return False

    current = application["status"]
    if current == "processing":
        return False
    if current != "submitted":
        return False

    now = _utcnow()
    await connection.execute(
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
            'submitted'::application_status,
            'processing'::application_status,
            'system'::audit_actor_type,
            NULL,
            %s,
            %s
        )
        """,
        (application_id, reason, now),
    )
    await connection.execute(
        """
        UPDATE applications
           SET status = 'processing'::application_status,
               updated_at = %s
         WHERE id = %s
        """,
        (now, application_id),
    )
    record_application_status("processing")
    return True
