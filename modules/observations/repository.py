"""Raw SQL persistence for labelled AI observations (psycopg only)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from modules.observations.schemas import ObservationOutput

_AI_OBS_RETURNING = """
    id,
    application_id,
    processing_run_id,
    model_id,
    observation_text,
    risk_factors_json,
    strengths_json,
    cited_chunk_ids_json,
    is_ai_generated,
    claim_fence,
    created_at
"""

_AI_OBS_SELECT = """
    id,
    application_id,
    processing_run_id,
    model_id,
    observation_text,
    risk_factors_json,
    strengths_json,
    cited_chunk_ids_json,
    is_ai_generated,
    created_at
"""


async def record_ai_observation(
    connection: Any,
    application_id: UUID,
    processing_run_id: UUID,
    output: ObservationOutput,
    claim_fence: int,
    *,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    """
    Persist an immutable AI observation row.

    ``claim_fence`` must be the worker's active fence for the processing run.
    ``is_ai_generated`` is always stored as true (advisory BR-12 label).
    """
    if claim_fence < 0:
        raise ValueError("claim_fence must be >= 0")

    cited_ids = [str(chunk_id) for chunk_id in output.cited_chunk_ids]
    row = await connection.execute(
        f"""
        INSERT INTO assessment_ai_observations (
            application_id,
            processing_run_id,
            model_id,
            observation_text,
            risk_factors_json,
            strengths_json,
            cited_chunk_ids_json,
            is_ai_generated,
            claim_fence,
            created_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, true, %s,
            COALESCE(%s, now())
        )
        RETURNING {_AI_OBS_RETURNING}
        """,
        (
            application_id,
            processing_run_id,
            output.model_id,
            output.observation_text,
            Jsonb(list(output.risk_factors)),
            Jsonb(list(output.strengths)),
            Jsonb(cited_ids),
            claim_fence,
            created_at,
        ),
    )
    inserted = await row.fetchone()
    assert inserted is not None
    return dict(inserted)


async def fetch_latest_ai_observation(
    connection: Any,
    application_id: UUID,
) -> dict[str, Any] | None:
    """
    Read-only: latest observation for ``application_id``.

    Always filters on ``application_id`` (tenant isolation). SELECT only.
    """
    row = await connection.execute(
        f"""
        SELECT {_AI_OBS_SELECT}
          FROM assessment_ai_observations
         WHERE application_id = %s
         ORDER BY created_at DESC, id DESC
         LIMIT 1
        """,
        (application_id,),
    )
    result = await row.fetchone()
    return dict(result) if result is not None else None


async def fetch_ai_observation_by_run_id(
    connection: Any,
    application_id: UUID,
    processing_run_id: UUID,
) -> dict[str, Any] | None:
    """
    Read-only: observation for ``processing_run_id`` scoped to ``application_id``.

    Returns None when missing or when the run belongs to another application.
    """
    row = await connection.execute(
        f"""
        SELECT {_AI_OBS_SELECT}
          FROM assessment_ai_observations
         WHERE application_id = %s
           AND processing_run_id = %s
        """,
        (application_id, processing_run_id),
    )
    result = await row.fetchone()
    return dict(result) if result is not None else None


async def fetch_snapshot_facts(
    connection: Any,
    application_id: UUID,
    input_snapshot_id: UUID,
) -> dict[str, Any] | None:
    """Load frozen snapshot facts for observation context assembly."""
    row = await connection.execute(
        """
        SELECT
            applicant_type::text AS applicant_type,
            requested_amount,
            tenure_months,
            declared_net_monthly_income,
            declared_existing_emis,
            as_of_date
          FROM application_input_snapshots
         WHERE id = %s
           AND application_id = %s
        """,
        (input_snapshot_id, application_id),
    )
    result = await row.fetchone()
    return dict(result) if result is not None else None


async def fetch_findings_for_processing_run(
    connection: Any,
    application_id: UUID,
    processing_run_id: UUID,
) -> list[dict[str, Any]]:
    """Load verification findings for the processing run (same application)."""
    row = await connection.execute(
        """
        SELECT
            f.rule_code,
            f.rule_name,
            f.status::text AS status,
            f.severity::text AS severity,
            f.message
          FROM verification_findings AS f
          JOIN verification_runs AS r
            ON r.id = f.verification_run_id
           AND r.application_id = f.application_id
         WHERE r.application_id = %s
           AND r.processing_run_id = %s
         ORDER BY f.rule_code ASC, f.id ASC
        """,
        (application_id, processing_run_id),
    )
    rows = await row.fetchall()
    return [dict(item) for item in rows]


async def fetch_financial_calculation_for_run(
    connection: Any,
    application_id: UUID,
    processing_run_id: UUID,
) -> dict[str, Any] | None:
    """Load financial calculation metrics for the processing run."""
    row = await connection.execute(
        """
        SELECT
            proposed_emi,
            total_obligations,
            foir_pct,
            foir_threshold_pct,
            foir_passed,
            net_disposable_income,
            max_qualifying_loan_amount
          FROM application_financial_calculations
         WHERE application_id = %s
           AND processing_run_id = %s
        """,
        (application_id, processing_run_id),
    )
    result = await row.fetchone()
    return dict(result) if result is not None else None
