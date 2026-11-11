"""Resumable modular workflow runner with fenced checkpoints (ADR-006, ADR-015)."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from psycopg.errors import RaiseException
from psycopg_pool import AsyncConnectionPool

from apps.api.metrics import ProcessingRunTimer, record_application_status
from modules.governance.audit import append_audit_event
from modules.worker import checkpoints, queue
from modules.worker.nodes.calculate import calculate_financials
from modules.worker.nodes.classify import classify_documents
from modules.worker.nodes.extract import extract_documents
from modules.worker.nodes.observe import generate_observations_node
from modules.worker.nodes.policy_retrieval import retrieve_policy_citations
from modules.worker.nodes.verify import verify_rules
from modules.worker.schemas import RunClaim, TerminalRunStatus

logger = logging.getLogger("loanlens.worker")

WorkflowState = dict[str, Any]
NodeHandler = Callable[[WorkflowState], Awaitable[WorkflowState]]
PipelineStep = tuple[str, NodeHandler]

# Ephemeral key injected for node handlers; never written to checkpoints.
_DB_POOL_STATE_KEY = "_db_pool"


class StaleWorkerFenceError(RuntimeError):
    """Raised when a checkpoint write is rejected by the fence trigger."""

    def __init__(self, message: str = "STALE_WORKER_FENCE") -> None:
        super().__init__(message)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _is_stale_fence_error(exc: BaseException) -> bool:
    return "STALE_WORKER_FENCE" in str(exc)


def _jsonable_id(value: UUID | str | None) -> str | None:
    if value is None:
        return None
    return str(value)


def _seed_run_state(state: WorkflowState, claim: RunClaim) -> WorkflowState:
    """Ensure claim identifiers are present for pipeline nodes (JSON-safe)."""
    seeded = dict(state)
    seeded.setdefault("application_id", str(claim.application_id))
    seeded.setdefault("processing_run_id", str(claim.id))
    seeded.setdefault("claim_fence", claim.claim_fence)
    if "input_snapshot_id" not in seeded:
        seeded["input_snapshot_id"] = _jsonable_id(claim.input_snapshot_id)
    return seeded


def _checkpoint_safe_state(state: WorkflowState) -> WorkflowState:
    """Drop non-serializable handler injection before checkpoint persistence."""
    cleaned = dict(state)
    cleaned.pop(_DB_POOL_STATE_KEY, None)
    for key in ("application_id", "processing_run_id", "input_snapshot_id"):
        if key in cleaned and isinstance(cleaned[key], UUID):
            cleaned[key] = str(cleaned[key])
    return cleaned


PLACEHOLDER_PIPELINE: list[PipelineStep] = [
    ("classify", classify_documents),
    ("extract", extract_documents),
    ("verify", verify_rules),
    ("calculate", calculate_financials),
    ("policy_retrieval", retrieve_policy_citations),
    ("observe", generate_observations_node),
]


async def transition_application_from_processing(
    connection: Any,
    application_id: UUID,
    *,
    to_status: str,
    reason: str,
) -> bool:
    """
    Move application processing → awaiting_human_review | processing_failed.

    Inserts application_state_history in the same transaction (DB trigger).
    """
    if to_status not in ("awaiting_human_review", "processing_failed"):
        raise ValueError(
            "to_status must be 'awaiting_human_review' or 'processing_failed'"
        )

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
    if current == to_status:
        return False
    if current != "processing":
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
            'processing'::application_status,
            %s::application_status,
            'system'::audit_actor_type,
            NULL,
            %s,
            %s
        )
        """,
        (application_id, to_status, reason, now),
    )
    await connection.execute(
        """
        UPDATE applications
           SET status = %s::application_status,
               updated_at = %s
         WHERE id = %s
        """,
        (to_status, now, application_id),
    )
    record_application_status(to_status)
    return True


async def run_workflow(
    pool: AsyncConnectionPool,
    claim: RunClaim,
    steps: Sequence[PipelineStep],
    *,
    request_id: str | None = None,
    initial_state: WorkflowState | None = None,
) -> TerminalRunStatus:
    """
    Execute ordered node handlers with checkpoint resume.

    Succeeded checkpoints are never re-executed. A ``STALE_WORKER_FENCE``
    error aborts immediately without failing the run (ADR-015).
    """
    if not steps:
        raise ValueError("steps must be non-empty")

    timer = ProcessingRunTimer()
    correlation = {
        "request_id": request_id,
        "application_id": str(claim.application_id),
        "processing_run_id": str(claim.id),
        "claim_fence": claim.claim_fence,
    }

    async with pool.connection() as conn:
        latest = await checkpoints.get_latest_checkpoint(conn, claim.id)
        completed_nodes = await checkpoints.list_succeeded_node_names(conn, claim.id)
        await conn.commit()

    state: WorkflowState = dict(initial_state) if initial_state is not None else {}
    next_sequence = 1
    if latest is not None:
        next_sequence = latest.sequence_no + 1
        if latest.status == "succeeded":
            state = dict(latest.state_json)
        elif latest.status == "failed":
            # Prior attempt failed mid-run without terminalizing; re-fail cleanly.
            async with pool.connection() as conn:
                await _fail_run(
                    conn,
                    claim,
                    error_code=latest.error_code or "checkpoint_failed",
                    error_message=(
                        f"Resumed run already has failed checkpoint "
                        f"for node {latest.node_name}"
                    ),
                    request_id=request_id,
                )
                await conn.commit()
            timer.observe("failed")
            return "failed"

    state = _seed_run_state(state, claim)

    for node_name, handler in steps:
        if node_name in completed_nodes:
            logger.info(
                "worker_node_skipped",
                extra={**correlation, "node_name": node_name},
            )
            continue

        try:
            async with pool.connection() as conn:
                await checkpoints.record_node_event(
                    conn,
                    claim.application_id,
                    claim.id,
                    node_name,
                    "started",
                    detail_json={"sequence_no": next_sequence},
                )
                await conn.commit()

            invoke_state = dict(state)
            invoke_state[_DB_POOL_STATE_KEY] = pool
            state = _checkpoint_safe_state(await handler(invoke_state))

            async with pool.connection() as conn:
                await checkpoints.save_checkpoint(
                    conn,
                    claim.application_id,
                    claim.id,
                    claim.claim_fence,
                    node_name,
                    next_sequence,
                    "succeeded",
                    state,
                )
                await checkpoints.record_node_event(
                    conn,
                    claim.application_id,
                    claim.id,
                    node_name,
                    "completed",
                    detail_json={"sequence_no": next_sequence},
                )
                await conn.commit()
        except RaiseException as exc:
            if _is_stale_fence_error(exc):
                logger.warning(
                    "worker_stale_fence_abort",
                    extra={**correlation, "node_name": node_name},
                )
                raise StaleWorkerFenceError(str(exc)) from exc
            await _handle_node_failure(
                pool,
                claim,
                node_name=node_name,
                sequence_no=next_sequence,
                state=state,
                error_code="node_db_error",
                error_message=str(exc)[:2000],
                request_id=request_id,
            )
            timer.observe("failed")
            return "failed"
        except StaleWorkerFenceError:
            raise
        except Exception as exc:  # noqa: BLE001 — node failure becomes failed run
            await _handle_node_failure(
                pool,
                claim,
                node_name=node_name,
                sequence_no=next_sequence,
                state=state,
                error_code=getattr(exc, "code", None) or type(exc).__name__,
                error_message=str(exc)[:2000],
                request_id=request_id,
            )
            timer.observe("failed")
            return "failed"

        next_sequence += 1

    async with pool.connection() as conn:
        try:
            completed = await queue.complete_run(
                conn,
                claim.id,
                claim.claim_fence,
                "succeeded",
            )
            if not completed:
                # Fence lost between last checkpoint and completion: abort.
                await conn.rollback()
                logger.warning(
                    "worker_complete_rejected_abort",
                    extra=correlation,
                )
                raise StaleWorkerFenceError(
                    "STALE_WORKER_FENCE: complete_run rejected"
                )

            await transition_application_from_processing(
                conn,
                claim.application_id,
                to_status="awaiting_human_review",
                reason="processing_succeeded",
            )
            await conn.commit()
        except RaiseException as exc:
            await conn.rollback()
            if _is_stale_fence_error(exc):
                raise StaleWorkerFenceError(str(exc)) from exc
            raise

    timer.observe("succeeded")
    logger.info("worker_workflow_succeeded", extra=correlation)
    return "succeeded"


async def _handle_node_failure(
    pool: AsyncConnectionPool,
    claim: RunClaim,
    *,
    node_name: str,
    sequence_no: int,
    state: WorkflowState,
    error_code: str,
    error_message: str,
    request_id: str | None,
) -> None:
    try:
        async with pool.connection() as conn:
            await checkpoints.save_checkpoint(
                conn,
                claim.application_id,
                claim.id,
                claim.claim_fence,
                node_name,
                sequence_no,
                "failed",
                _checkpoint_safe_state(state),
                error_code=error_code,
            )
            await checkpoints.record_node_event(
                conn,
                claim.application_id,
                claim.id,
                node_name,
                "failed",
                detail_json={
                    "sequence_no": sequence_no,
                    "error_code": error_code,
                    "error_message": error_message,
                },
            )
            await _fail_run(
                conn,
                claim,
                error_code=error_code,
                error_message=error_message,
                request_id=request_id,
            )
            await conn.commit()
    except RaiseException as exc:
        if _is_stale_fence_error(exc):
            logger.warning(
                "worker_stale_fence_on_failure_path",
                extra={
                    "application_id": str(claim.application_id),
                    "processing_run_id": str(claim.id),
                    "claim_fence": claim.claim_fence,
                    "node_name": node_name,
                },
            )
            raise StaleWorkerFenceError(str(exc)) from exc
        raise


async def _fail_run(
    connection: Any,
    claim: RunClaim,
    *,
    error_code: str,
    error_message: str,
    request_id: str | None,
) -> None:
    await queue.complete_run(
        connection,
        claim.id,
        claim.claim_fence,
        "failed",
        error_code=error_code,
        error_message=error_message,
    )
    await transition_application_from_processing(
        connection,
        claim.application_id,
        to_status="processing_failed",
        reason="processing_failed",
    )
    await append_audit_event(
        connection,
        action="processing.failed",
        actor_type="system",
        actor_user_id=None,
        application_id=claim.application_id,
        entity_type="processing_run",
        entity_id=claim.id,
        rationale=error_message[:500],
        after_json={
            "processing_run_id": str(claim.id),
            "error_code": error_code,
            "claim_fence": claim.claim_fence,
        },
        request_id=request_id,
    )
