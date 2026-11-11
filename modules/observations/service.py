"""AI observation orchestration (assemble → generate → persist / query)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from modules.observations import repository
from modules.observations.engine import (
    build_observation_context,
    generate_observations,
)
from modules.observations.provider import ObservationProvider
from modules.observations.schemas import (
    AiObservationRecord,
    AiObservationResponse,
    ObservationContext,
    ObservationOutput,
)


def _as_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _as_uuid_list(value: object) -> list[UUID]:
    if not isinstance(value, list):
        return []
    result: list[UUID] = []
    for item in value:
        result.append(item if isinstance(item, UUID) else UUID(str(item)))
    return result


def _to_observation_response(row: dict[str, Any]) -> AiObservationResponse:
    return AiObservationResponse(
        id=row["id"],
        application_id=row["application_id"],
        processing_run_id=row["processing_run_id"],
        model_id=row["model_id"],
        observation_text=row["observation_text"],
        risk_factors=_as_string_list(row.get("risk_factors_json")),
        strengths=_as_string_list(row.get("strengths_json")),
        cited_chunk_ids=_as_uuid_list(row.get("cited_chunk_ids_json")),
        is_ai_generated=bool(row.get("is_ai_generated", True)),
        created_at=row["created_at"],
    )


async def assemble_observation_context(
    connection: Any,
    *,
    application_id: UUID,
    processing_run_id: UUID,
    input_snapshot_id: UUID,
    verification_findings: list[dict[str, Any]] | None = None,
    financial_calculation: dict[str, Any] | None = None,
    policy_citations: dict[str, list[dict[str, Any]]] | list[dict[str, Any]] | None = None,
) -> ObservationContext:
    """
    Build ObservationContext from workflow state, falling back to DB reads.
    """
    snapshot = await repository.fetch_snapshot_facts(
        connection, application_id, input_snapshot_id
    )

    findings = verification_findings
    if findings is None:
        findings = await repository.fetch_findings_for_processing_run(
            connection, application_id, processing_run_id
        )

    calculation = financial_calculation
    if calculation is None:
        calculation = await repository.fetch_financial_calculation_for_run(
            connection, application_id, processing_run_id
        )

    return build_observation_context(
        application_id=application_id,
        processing_run_id=processing_run_id,
        snapshot_facts=snapshot,
        verification_findings=findings,
        financial_calculation=calculation,
        policy_citations=policy_citations,
    )


async def record_ai_observation(
    connection: Any,
    application_id: UUID,
    processing_run_id: UUID,
    output: ObservationOutput,
    claim_fence: int,
) -> AiObservationRecord:
    """Service wrapper: persist observation with active claim fence."""
    row = await repository.record_ai_observation(
        connection,
        application_id,
        processing_run_id,
        output,
        claim_fence,
    )
    return AiObservationRecord.model_validate(row)


async def run_observation_generation(
    connection: Any,
    *,
    application_id: UUID,
    processing_run_id: UUID,
    input_snapshot_id: UUID,
    claim_fence: int,
    verification_findings: list[dict[str, Any]] | None = None,
    financial_calculation: dict[str, Any] | None = None,
    policy_citations: dict[str, list[dict[str, Any]]] | list[dict[str, Any]] | None = None,
    provider: ObservationProvider | None = None,
) -> tuple[ObservationContext, ObservationOutput, AiObservationRecord]:
    """
    Assemble context, generate advisory observations, and persist the row.

    Intended to run inside the caller's open transaction. Uses
    ``DeterministicObservationProvider`` by default (no live LLM).
    """
    context = await assemble_observation_context(
        connection,
        application_id=application_id,
        processing_run_id=processing_run_id,
        input_snapshot_id=input_snapshot_id,
        verification_findings=verification_findings,
        financial_calculation=financial_calculation,
        policy_citations=policy_citations,
    )
    output = generate_observations(context, provider=provider)
    record = await record_ai_observation(
        connection,
        application_id,
        processing_run_id,
        output,
        claim_fence,
    )
    return context, output, record


async def fetch_latest_ai_observation(
    connection: Any,
    application_id: UUID,
) -> AiObservationResponse | None:
    """Read-only: latest AI observation for the application."""
    row = await repository.fetch_latest_ai_observation(connection, application_id)
    if row is None:
        return None
    return _to_observation_response(row)


async def fetch_ai_observation_by_run_id(
    connection: Any,
    application_id: UUID,
    processing_run_id: UUID,
) -> AiObservationResponse | None:
    """
    Read-only: observation for a processing run, scoped to ``application_id``.
    """
    row = await repository.fetch_ai_observation_by_run_id(
        connection, application_id, processing_run_id
    )
    if row is None:
        return None
    return _to_observation_response(row)
