"""Worker orchestration: claim, application sync, lease renewal, completion."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any
from uuid import UUID, uuid4

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from modules.worker import queue
from modules.worker.runner import (
    PLACEHOLDER_PIPELINE,
    PipelineStep,
    StaleWorkerFenceError,
    run_workflow,
)
from modules.worker.schemas import RunClaim, TerminalRunStatus

logger = logging.getLogger("loanlens.worker")


async def claim_and_prepare(
    connection: AsyncConnection[Any],
    *,
    worker_id: str,
    lease_seconds: int,
) -> RunClaim | None:
    """
    Claim next run and, when the application is submitted, move it to processing.

    Claim and application status sync share one transaction (Architecture §11).
    """
    claim = await queue.claim_next_run(connection, worker_id, lease_seconds)
    if claim is None:
        return None

    await queue.transition_application_to_processing(
        connection,
        claim.application_id,
        reason="worker_claim",
    )
    return claim


async def renew_lease_loop(
    pool: AsyncConnectionPool,
    *,
    run_id: UUID,
    claim_fence: int,
    lease_seconds: int,
    stop: asyncio.Event,
    interval_seconds: float | None = None,
) -> None:
    """Periodically renew the lease until stop is set or the fence is lost."""
    interval = interval_seconds
    if interval is None:
        interval = max(1.0, lease_seconds / 3.0)

    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            break
        except TimeoutError:
            pass

        if stop.is_set():
            break

        async with pool.connection() as conn:
            renewed = await queue.renew_lease(
                conn,
                run_id,
                claim_fence,
                lease_seconds,
            )
            await conn.commit()

        if not renewed:
            logger.warning(
                "worker_lease_renew_lost_fence",
                extra={
                    "processing_run_id": str(run_id),
                    "claim_fence": claim_fence,
                },
            )
            stop.set()
            break


async def process_claimed_run(
    pool: AsyncConnectionPool,
    claim: RunClaim,
    *,
    lease_seconds: int,
    request_id: str | None = None,
    steps: Sequence[PipelineStep] | None = None,
) -> TerminalRunStatus | None:
    """
    Hold the lease while the checkpointed pipeline runs, then complete.

    Returns ``None`` when a stale fence aborts execution without terminalizing
    the run (ADR-015).
    """
    pipeline = list(steps) if steps is not None else list(PLACEHOLDER_PIPELINE)
    correlation = {
        "request_id": request_id or str(uuid4()),
        "application_id": str(claim.application_id),
        "processing_run_id": str(claim.id),
    }
    logger.info("worker_run_started", extra=correlation)

    stop = asyncio.Event()
    renewer = asyncio.create_task(
        renew_lease_loop(
            pool,
            run_id=claim.id,
            claim_fence=claim.claim_fence,
            lease_seconds=lease_seconds,
            stop=stop,
        ),
        name=f"lease-renew-{claim.id}",
    )

    terminal: TerminalRunStatus | None = None
    cancelled = False
    try:
        terminal = await run_workflow(
            pool,
            claim,
            pipeline,
            request_id=correlation["request_id"],
        )
    except StaleWorkerFenceError:
        terminal = None
        logger.warning("worker_run_aborted_stale_fence", extra=correlation)
    except asyncio.CancelledError:
        cancelled = True
        raise
    except Exception:  # noqa: BLE001 — unexpected errors outside node handling
        logger.exception("worker_run_failed", extra=correlation)
        # Runner already terminalizes node failures; unexpected errors here
        # mean the pipeline did not finish — leave lease for reclaim unless
        # cancelled. Do not mark failed without a fence-safe write path.
        terminal = None
        raise
    finally:
        stop.set()
        renewer.cancel()
        try:
            await renewer
        except asyncio.CancelledError:
            pass

        if cancelled:
            pass
        elif terminal is not None:
            logger.info(
                "worker_run_finished",
                extra={**correlation, "status": terminal},
            )

    return terminal


async def release_active_claim(
    pool: AsyncConnectionPool,
    claim: RunClaim,
) -> None:
    """On shutdown, expire the lease without failing the run (ADR-015)."""
    async with pool.connection() as conn:
        released = await queue.release_lease(conn, claim.id, claim.claim_fence)
        await conn.commit()
    logger.info(
        "worker_run_lease_released",
        extra={
            "application_id": str(claim.application_id),
            "processing_run_id": str(claim.id),
            "claim_fence": claim.claim_fence,
            "released": released,
        },
    )
