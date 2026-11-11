"""Workflow checkpoint and node-event persistence (ADR-006, ADR-015)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from modules.worker.schemas import CheckpointRecord, CheckpointStatus


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def save_checkpoint(
    connection: AsyncConnection[Any],
    application_id: UUID,
    processing_run_id: UUID,
    claim_fence: int,
    node_name: str,
    sequence_no: int,
    status: CheckpointStatus,
    state_json: dict[str, Any],
    *,
    error_code: str | None = None,
) -> UUID:
    """
    Insert a workflow checkpoint.

    ``claim_fence`` must equal ``processing_runs.fence_token`` or the DB
    raises ``STALE_WORKER_FENCE`` (ADR-015).
    """
    if not node_name.strip():
        raise ValueError("node_name must be non-empty")
    if sequence_no < 1:
        raise ValueError("sequence_no must be >= 1")
    if status == "failed" and (error_code is None or not error_code.strip()):
        raise ValueError("error_code is required when status is 'failed'")

    now = _utcnow()
    row = await connection.execute(
        """
        INSERT INTO workflow_checkpoints (
            application_id,
            processing_run_id,
            claim_fence,
            node_name,
            sequence_no,
            status,
            state_json,
            error_code,
            created_at,
            updated_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
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
            Jsonb(state_json),
            error_code if status == "failed" else None,
            now,
            now,
        ),
    )
    result = await row.fetchone()
    assert result is not None
    return result["id"]


async def get_latest_checkpoint(
    connection: AsyncConnection[Any],
    processing_run_id: UUID,
) -> CheckpointRecord | None:
    """Return the checkpoint with the highest sequence_no for the run, if any."""
    row = await connection.execute(
        """
        SELECT
            id,
            application_id,
            processing_run_id,
            claim_fence,
            node_name,
            sequence_no,
            status,
            state_json,
            error_code,
            created_at,
            updated_at
          FROM workflow_checkpoints
         WHERE processing_run_id = %s
         ORDER BY sequence_no DESC
         LIMIT 1
        """,
        (processing_run_id,),
    )
    result = await row.fetchone()
    if result is None:
        return None
    return CheckpointRecord.model_validate(dict(result))


async def list_succeeded_node_names(
    connection: AsyncConnection[Any],
    processing_run_id: UUID,
) -> set[str]:
    """Node names with a succeeded checkpoint for this run (resume skip set)."""
    row = await connection.execute(
        """
        SELECT node_name
          FROM workflow_checkpoints
         WHERE processing_run_id = %s
           AND status = 'succeeded'
        """,
        (processing_run_id,),
    )
    rows = await row.fetchall()
    return {str(item["node_name"]) for item in rows}


async def record_node_event(
    connection: AsyncConnection[Any],
    application_id: UUID,
    processing_run_id: UUID,
    node_name: str,
    status: str,
    detail_json: dict[str, Any] | None = None,
) -> UUID:
    """Append an immutable row to workflow_node_events."""
    if not node_name.strip():
        raise ValueError("node_name must be non-empty")
    if not status.strip():
        raise ValueError("status must be non-empty")

    detail = detail_json if detail_json is not None else {}
    now = _utcnow()
    row = await connection.execute(
        """
        INSERT INTO workflow_node_events (
            application_id,
            processing_run_id,
            node_name,
            status,
            detail_json,
            created_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s
        )
        RETURNING id
        """,
        (
            application_id,
            processing_run_id,
            node_name,
            status,
            Jsonb(detail),
            now,
        ),
    )
    result = await row.fetchone()
    assert result is not None
    return result["id"]
