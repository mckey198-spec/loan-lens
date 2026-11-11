"""Lexical policy citation retrieval pipeline node (Architecture §9; ADR-016/020)."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from psycopg_pool import AsyncConnectionPool

from modules.policy.schemas import PolicyChunkMatch
from modules.policy.search import (
    retrieve_relevant_citations,
    topics_for_findings,
)

logger = logging.getLogger("loanlens.worker")

WorkflowState = dict[str, Any]


def _as_uuid(value: object, field: str) -> UUID:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str) and value:
        return UUID(value)
    raise ValueError(f"workflow state missing valid {field}")


def _serialize_match(match: PolicyChunkMatch) -> dict[str, Any]:
    return {
        "chunk_id": str(match.chunk_id),
        "section_label": match.section_label,
        "content_text": match.content_text,
        "rank_score": match.rank_score,
    }


def _parse_findings(raw: object) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    findings: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            findings.append(item)
    return findings


def _parse_calculation(raw: object) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        return raw
    return None


async def retrieve_policy_citations(state: WorkflowState) -> WorkflowState:
    """
    Pipeline node: resolve the snapshot's frozen policy version and retrieve
    lexical citations for topics relevant to verification / calculation results.

    Injects ``policy_citations`` into workflow state. Never searches across
    unbound policy versions. Dense retrieval is off (ADR-020).
    """
    pool = state.get("_db_pool")
    if not isinstance(pool, AsyncConnectionPool):
        raise ValueError(
            "retrieve_policy_citations requires _db_pool in workflow state"
        )

    application_id = _as_uuid(state.get("application_id"), "application_id")
    processing_run_id = _as_uuid(
        state.get("processing_run_id"),
        "processing_run_id",
    )

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
            raise ValueError("retrieve_policy_citations requires input_snapshot_id")
        input_snapshot_id = run["input_snapshot_id"]
    else:
        input_snapshot_id = _as_uuid(input_snapshot_raw, "input_snapshot_id")

    topics = topics_for_findings(
        verification_findings=_parse_findings(state.get("verification_findings")),
        financial_calculation=_parse_calculation(state.get("financial_calculation")),
        include_defaults=True,
    )

    async with pool.connection() as conn:
        snapshot_row = await conn.execute(
            """
            SELECT applicable_policy_version_id
              FROM application_input_snapshots
             WHERE id = %s
               AND application_id = %s
            """,
            (input_snapshot_id, application_id),
        )
        snapshot = await snapshot_row.fetchone()
        if snapshot is None:
            raise ValueError(
                "retrieve_policy_citations: input snapshot not found for application"
            )
        policy_version_id = snapshot["applicable_policy_version_id"]
        if policy_version_id is None:
            raise ValueError(
                "retrieve_policy_citations: snapshot has no applicable_policy_version_id"
            )

        citations = await retrieve_relevant_citations(
            conn,
            policy_version_id,
            topics,
        )
        await conn.commit()

    serialized: dict[str, list[dict[str, Any]]] = {
        topic: [_serialize_match(match) for match in matches]
        for topic, matches in citations.items()
    }

    hit_topics = sum(1 for matches in citations.values() if matches)
    logger.info(
        "policy_citations_retrieved",
        extra={
            "application_id": str(application_id),
            "processing_run_id": str(processing_run_id),
            "input_snapshot_id": str(input_snapshot_id),
            "policy_version_id": str(policy_version_id),
            "topic_count": len(topics),
            "topics_with_hits": hit_topics,
        },
    )

    updated = dict(state)
    updated.pop("_db_pool", None)
    updated["input_snapshot_id"] = str(input_snapshot_id)
    updated["applicable_policy_version_id"] = str(policy_version_id)
    updated["policy_citations"] = serialized
    return updated
