"""Labelled AI observation pipeline node (Architecture §8; BR-12)."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from psycopg_pool import AsyncConnectionPool

from modules.observations.provider import DeterministicObservationProvider
from modules.observations.service import run_observation_generation

logger = logging.getLogger("loanlens.worker")

WorkflowState = dict[str, Any]

_DEFAULT_PROVIDER = DeterministicObservationProvider()


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


def _parse_findings(raw: object) -> list[dict[str, Any]] | None:
    if raw is None:
        return None
    if not isinstance(raw, list):
        return None
    findings: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            findings.append(item)
    return findings


def _parse_calculation(raw: object) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        return raw
    return None


def _parse_citations(
    raw: object,
) -> dict[str, list[dict[str, Any]]] | list[dict[str, Any]] | None:
    if isinstance(raw, dict) or isinstance(raw, list):
        return raw  # type: ignore[return-value]
    return None


async def generate_observations_node(state: WorkflowState) -> WorkflowState:
    """
    Pipeline node: assemble snapshot / findings / calculations / citations,
    generate labelled advisory observations, and persist
    ``assessment_ai_observations`` with the active claim fence.

    Injects observations into workflow state. Never assigns approval /
    rejection outcomes or risk grades (BR-12 advisory only).
    """
    pool = state.get("_db_pool")
    if not isinstance(pool, AsyncConnectionPool):
        raise ValueError(
            "generate_observations_node requires _db_pool in workflow state"
        )

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
            raise ValueError(
                "generate_observations_node requires input_snapshot_id"
            )
        input_snapshot_id = run["input_snapshot_id"]
    else:
        input_snapshot_id = _as_uuid(input_snapshot_raw, "input_snapshot_id")

    async with pool.connection() as conn:
        async with conn.transaction():
            _context, output, record = await run_observation_generation(
                conn,
                application_id=application_id,
                processing_run_id=processing_run_id,
                input_snapshot_id=input_snapshot_id,
                claim_fence=claim_fence,
                verification_findings=_parse_findings(
                    state.get("verification_findings")
                ),
                financial_calculation=_parse_calculation(
                    state.get("financial_calculation")
                ),
                policy_citations=_parse_citations(state.get("policy_citations")),
                provider=_DEFAULT_PROVIDER,
            )

    logger.info(
        "ai_observation_completed",
        extra={
            "application_id": str(application_id),
            "processing_run_id": str(processing_run_id),
            "input_snapshot_id": str(input_snapshot_id),
            "claim_fence": claim_fence,
            "ai_observation_id": str(record.id),
            "model_id": output.model_id,
            "is_ai_generated": True,
            "cited_chunk_count": len(output.cited_chunk_ids),
            "risk_factor_count": len(output.risk_factors),
            "strength_count": len(output.strengths),
        },
    )

    updated = dict(state)
    updated.pop("_db_pool", None)
    updated["input_snapshot_id"] = str(input_snapshot_id)
    updated["ai_observation_id"] = str(record.id)
    updated["ai_observation"] = {
        "id": str(record.id),
        "model_id": output.model_id,
        "observation_text": output.observation_text,
        "risk_factors": list(output.risk_factors),
        "strengths": list(output.strengths),
        "cited_chunk_ids": [str(cid) for cid in output.cited_chunk_ids],
        "is_ai_generated": True,
    }
    return updated
