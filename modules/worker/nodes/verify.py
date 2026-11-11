"""Deterministic verification pipeline node (Architecture §8; BR-01…BR-11)."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from psycopg_pool import AsyncConnectionPool

from modules.verification.schemas import RecurringDebitObservation
from modules.verification.service import run_verification

logger = logging.getLogger("loanlens.worker")

WorkflowState = dict[str, Any]


def _as_uuid(value: object, field: str) -> UUID:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str) and value:
        return UUID(value)
    raise ValueError(f"workflow state missing valid {field}")


def _as_fence(value: object) -> int:
    if isinstance(value, bool) or value is None:
        raise ValueError("workflow state missing valid claim_fence")
    return int(value)


def _parse_recurring_debits(
    raw: object,
) -> list[RecurringDebitObservation]:
    if not isinstance(raw, list):
        return []
    debits: list[RecurringDebitObservation] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        debits.append(RecurringDebitObservation.model_validate(item))
    return debits


def _parse_correction_overlays(
    raw: object,
) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        return {}
    overlays: dict[str, dict[str, Any]] = {}
    for key, value in raw.items():
        if isinstance(value, dict):
            overlays[str(key)] = value
    return overlays


async def verify_rules(state: WorkflowState) -> WorkflowState:
    """
    Pipeline node: resolve layered field values, evaluate BR-01…BR-11,
    and persist immutable verification_runs / verification_findings.
    """
    pool = state.get("_db_pool")
    if not isinstance(pool, AsyncConnectionPool):
        raise ValueError("verify_rules requires _db_pool in workflow state")

    application_id = _as_uuid(state.get("application_id"), "application_id")
    processing_run_id = _as_uuid(
        state.get("processing_run_id"),
        "processing_run_id",
    )
    claim_fence = _as_fence(state.get("claim_fence"))

    input_snapshot_raw = state.get("input_snapshot_id")
    if input_snapshot_raw is None or input_snapshot_raw == "":
        async with pool.connection() as conn:
            run_row = await conn.execute(
                """
                SELECT input_snapshot_id
                  FROM processing_runs
                 WHERE id = %s
                   AND application_id = %s
                """,
                (processing_run_id, application_id),
            )
            run = await run_row.fetchone()
            await conn.commit()
        if run is None or run["input_snapshot_id"] is None:
            raise ValueError("verify_rules requires input_snapshot_id")
        input_snapshot_id = run["input_snapshot_id"]
    else:
        input_snapshot_id = _as_uuid(input_snapshot_raw, "input_snapshot_id")

    correction_overlays = _parse_correction_overlays(
        state.get("field_corrections")
    )
    recurring_debits = _parse_recurring_debits(state.get("recurring_debits"))

    async with pool.connection() as conn:
        async with conn.transaction():
            _context, results, run, findings = await run_verification(
                conn,
                application_id=application_id,
                processing_run_id=processing_run_id,
                input_snapshot_id=input_snapshot_id,
                claim_fence=claim_fence,
                correction_overlays=correction_overlays,
                recurring_debits=recurring_debits,
            )

    logger.info(
        "verification_completed",
        extra={
            "application_id": str(application_id),
            "processing_run_id": str(processing_run_id),
            "claim_fence": claim_fence,
            "verification_run_id": str(run.id),
            "total_rules_evaluated": run.total_rules_evaluated,
            "passed_count": run.passed_count,
            "flagged_count": run.flagged_count,
            "failed_count": run.failed_count,
        },
    )

    updated = dict(state)
    updated.pop("_db_pool", None)
    updated["input_snapshot_id"] = str(input_snapshot_id)
    updated["verification_run_id"] = str(run.id)
    updated["verification_summary"] = {
        "total_rules_evaluated": run.total_rules_evaluated,
        "passed_count": run.passed_count,
        "flagged_count": run.flagged_count,
        "failed_count": run.failed_count,
    }
    updated["verification_findings"] = [
        {
            "rule_code": finding.rule_code,
            "rule_name": finding.rule_name,
            "status": finding.status,
            "severity": finding.severity,
            "message": finding.message,
        }
        for finding in findings
    ]
    updated["rule_results"] = [
        {
            "rule_code": result.rule_code,
            "status": result.status,
            "severity": result.severity,
        }
        for result in results
    ]
    return updated
