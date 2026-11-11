"""LoanLens worker process: PostgreSQL SKIP LOCKED claim loop (M05-T02/T03)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

# psycopg async cannot use ProactorEventLoop (default on Windows).
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from shared.db import create_pool
from shared.logging import setup_logging
from shared.settings import get_settings
from modules.worker.runner import PLACEHOLDER_PIPELINE
from modules.worker.schemas import RunClaim
from modules.worker.service import (
    claim_and_prepare,
    process_claimed_run,
    release_active_claim,
)

setup_logging()
logger = logging.getLogger("loanlens.worker")

HEARTBEAT_INTERVAL_SECONDS = int(os.environ.get("WORKER_HEARTBEAT_INTERVAL_SECONDS", "30"))
POLL_IDLE_SECONDS = float(os.environ.get("WORKER_POLL_IDLE_SECONDS", "1"))
HEARTBEAT_PATH = Path(
    os.environ.get(
        "WORKER_HEARTBEAT_PATH",
        str(Path(tempfile.gettempdir()) / "worker_heartbeat.json"),
    )
)
WORKER_ID = os.environ.get("WORKER_ID") or f"worker-{uuid4()}"


def _write_heartbeat(path: Path) -> str:
    """Write a UTC ISO-8601 timestamp to the heartbeat file; return the timestamp."""
    heartbeat_at = datetime.now(timezone.utc).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"heartbeat_at": heartbeat_at, "worker_id": WORKER_ID}
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return heartbeat_at


async def _run_loop() -> None:
    settings = get_settings()
    lease_seconds = settings.WORKER_LEASE_TTL_SECONDS
    pool = create_pool(settings.DATABASE_URL)
    await pool.open()

    stop = asyncio.Event()
    active_claim: RunClaim | None = None
    processing_task: asyncio.Task[object] | None = None
    loop = asyncio.get_running_loop()

    def _request_shutdown() -> None:
        logger.info("worker_shutdown_requested")
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_shutdown)
        except NotImplementedError:
            # Windows: add_signal_handler is limited; fall back below.
            pass

    sigbreak = getattr(signal, "SIGBREAK", None)
    if sigbreak is not None:
        try:
            loop.add_signal_handler(sigbreak, _request_shutdown)
        except (NotImplementedError, RuntimeError, ValueError):
            pass

    # Sync signal handlers for environments without asyncio signal support.
    def _sync_signal(signum: int, _frame: object) -> None:
        logger.info("worker_shutdown_requested signal=%s", signum)
        loop.call_soon_threadsafe(stop.set)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _sync_signal)
        except (ValueError, OSError):
            pass
    if sigbreak is not None:
        try:
            signal.signal(sigbreak, _sync_signal)
        except (ValueError, OSError):
            pass

    logger.info(
        "worker_started worker_id=%s heartbeat_path=%s lease_seconds=%s",
        WORKER_ID,
        HEARTBEAT_PATH,
        lease_seconds,
    )

    last_heartbeat = 0.0
    try:
        while not stop.is_set():
            now_mono = asyncio.get_running_loop().time()
            if now_mono - last_heartbeat >= HEARTBEAT_INTERVAL_SECONDS:
                heartbeat_at = _write_heartbeat(HEARTBEAT_PATH)
                logger.info("worker_heartbeat heartbeat_at=%s", heartbeat_at)
                last_heartbeat = now_mono

            if processing_task is not None:
                if processing_task.done():
                    try:
                        processing_task.result()
                    except asyncio.CancelledError:
                        pass
                    except Exception:  # noqa: BLE001
                        logger.exception("worker_processing_task_error")
                    processing_task = None
                    active_claim = None
                else:
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=POLL_IDLE_SECONDS)
                    except TimeoutError:
                        pass
                    continue

            async with pool.connection() as conn:
                claim = await claim_and_prepare(
                    conn,
                    worker_id=WORKER_ID,
                    lease_seconds=lease_seconds,
                )
                await conn.commit()

            if claim is None:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=POLL_IDLE_SECONDS)
                except TimeoutError:
                    pass
                continue

            request_id = str(uuid4())
            logger.info(
                "worker_run_claimed",
                extra={
                    "request_id": request_id,
                    "application_id": str(claim.application_id),
                    "processing_run_id": str(claim.id),
                    "claim_fence": claim.claim_fence,
                    "locked_by": claim.locked_by,
                },
            )
            active_claim = claim
            processing_task = asyncio.create_task(
                process_claimed_run(
                    pool,
                    claim,
                    lease_seconds=lease_seconds,
                    request_id=request_id,
                    steps=PLACEHOLDER_PIPELINE,
                ),
                name=f"process-run-{claim.id}",
            )
    finally:
        if processing_task is not None and not processing_task.done():
            processing_task.cancel()
            try:
                await processing_task
            except asyncio.CancelledError:
                pass
            if active_claim is not None:
                await release_active_claim(pool, active_claim)
        await pool.close()
        logger.info("worker_stopped worker_id=%s", WORKER_ID)


def run() -> None:
    """Entrypoint used by Compose and local process managers."""
    asyncio.run(_run_loop())


if __name__ == "__main__":
    run()
