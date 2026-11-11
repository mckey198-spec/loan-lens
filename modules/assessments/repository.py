"""Raw SQL access for assessments, dispositions, and field corrections (no ORM)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

_ASSESSMENT_COLUMNS = """
    id,
    application_id,
    processing_run_id,
    input_snapshot_id,
    applicable_policy_version_id,
    assigned_reviewer_id,
    recommendation::text AS recommendation,
    reviewer_notes,
    decided_at,
    created_at,
    updated_at
"""

_DISPOSITION_COLUMNS = """
    id,
    application_id,
    assessment_id,
    verification_finding_id,
    action::text AS action,
    justification,
    created_by_user_id,
    created_at
"""

_CORRECTION_COLUMNS = """
    id,
    application_id,
    extracted_field_id,
    assessment_id,
    corrected_value_text,
    corrected_value_numeric,
    corrected_value_date,
    reason,
    corrected_by_user_id,
    created_at
"""


async def fetch_application(
    conn: AsyncConnection[Any],
    application_id: UUID,
    *,
    for_update: bool = False,
) -> dict[str, Any] | None:
    lock_clause = " FOR UPDATE" if for_update else ""
    row = await conn.execute(
        f"""
        SELECT
            id,
            status::text AS status,
            assigned_reviewer_user_id,
            applicable_policy_version_id
          FROM applications
         WHERE id = %s
        {lock_clause}
        """,
        (application_id,),
    )
    return await row.fetchone()


async def lock_application(
    conn: AsyncConnection[Any],
    application_id: UUID,
) -> dict[str, Any] | None:
    return await fetch_application(conn, application_id, for_update=True)


async def insert_state_history(
    conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    from_status: str,
    to_status: str,
    actor_user_id: UUID,
    reason: str,
    created_at: datetime,
) -> dict[str, Any]:
    row = await conn.execute(
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
            %s::application_status,
            %s::application_status,
            'user'::audit_actor_type,
            %s,
            %s,
            %s
        )
        RETURNING
            id,
            application_id,
            from_status::text AS from_status,
            to_status::text AS to_status,
            actor_user_id,
            reason,
            created_at
        """,
        (
            application_id,
            from_status,
            to_status,
            actor_user_id,
            reason,
            created_at,
        ),
    )
    result = await row.fetchone()
    assert result is not None
    return result


async def transition_application_status(
    conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    to_status: str,
    assigned_reviewer_user_id: UUID | None,
    updated_at: datetime,
) -> dict[str, Any] | None:
    row = await conn.execute(
        """
        UPDATE applications
           SET status = %s::application_status,
               assigned_reviewer_user_id = COALESCE(%s, assigned_reviewer_user_id),
               updated_at = %s
         WHERE id = %s
        RETURNING
            id,
            status::text AS status,
            assigned_reviewer_user_id,
            applicable_policy_version_id
        """,
        (to_status, assigned_reviewer_user_id, updated_at, application_id),
    )
    return await row.fetchone()


async def fetch_latest_succeeded_run(
    conn: AsyncConnection[Any],
    application_id: UUID,
) -> dict[str, Any] | None:
    row = await conn.execute(
        """
        SELECT
            pr.id AS processing_run_id,
            pr.input_snapshot_id,
            s.applicable_policy_version_id
          FROM processing_runs AS pr
          JOIN application_input_snapshots AS s
            ON s.id = pr.input_snapshot_id
           AND s.application_id = pr.application_id
         WHERE pr.application_id = %s
           AND pr.status = 'succeeded'::processing_run_status
           AND pr.input_snapshot_id IS NOT NULL
         ORDER BY pr.finished_at DESC NULLS LAST, pr.created_at DESC
         LIMIT 1
        """,
        (application_id,),
    )
    return await row.fetchone()


async def insert_assessment(
    conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    processing_run_id: UUID,
    input_snapshot_id: UUID,
    applicable_policy_version_id: UUID,
    assigned_reviewer_id: UUID,
    created_at: datetime,
) -> dict[str, Any]:
    row = await conn.execute(
        f"""
        INSERT INTO assessments (
            application_id,
            processing_run_id,
            input_snapshot_id,
            applicable_policy_version_id,
            assigned_reviewer_id,
            recommendation,
            reviewer_notes,
            decided_at,
            created_at,
            updated_at
        ) VALUES (
            %s, %s, %s, %s, %s,
            NULL, NULL, NULL, %s, %s
        )
        RETURNING {_ASSESSMENT_COLUMNS}
        """,
        (
            application_id,
            processing_run_id,
            input_snapshot_id,
            applicable_policy_version_id,
            assigned_reviewer_id,
            created_at,
            created_at,
        ),
    )
    result = await row.fetchone()
    assert result is not None
    return result


async def fetch_assessment(
    conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    assessment_id: UUID,
    for_update: bool = False,
) -> dict[str, Any] | None:
    lock_clause = " FOR UPDATE" if for_update else ""
    row = await conn.execute(
        f"""
        SELECT {_ASSESSMENT_COLUMNS}
          FROM assessments
         WHERE id = %s
           AND application_id = %s
        {lock_clause}
        """,
        (assessment_id, application_id),
    )
    return await row.fetchone()


async def list_assessments(
    conn: AsyncConnection[Any],
    application_id: UUID,
) -> list[dict[str, Any]]:
    rows = await conn.execute(
        f"""
        SELECT {_ASSESSMENT_COLUMNS}
          FROM assessments
         WHERE application_id = %s
         ORDER BY created_at DESC, id DESC
        """,
        (application_id,),
    )
    return list(await rows.fetchall())


async def decide_assessment(
    conn: AsyncConnection[Any],
    *,
    assessment_id: UUID,
    recommendation: str,
    reviewer_notes: str,
    decided_at: datetime,
) -> dict[str, Any] | None:
    row = await conn.execute(
        f"""
        UPDATE assessments
           SET recommendation = %s::assessment_recommendation,
               reviewer_notes = %s,
               decided_at = %s,
               updated_at = %s
         WHERE id = %s
        RETURNING {_ASSESSMENT_COLUMNS}
        """,
        (recommendation, reviewer_notes, decided_at, decided_at, assessment_id),
    )
    return await row.fetchone()


async def fetch_verification_finding(
    conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    finding_id: UUID,
) -> dict[str, Any] | None:
    row = await conn.execute(
        """
        SELECT id, application_id, verification_run_id
          FROM verification_findings
         WHERE id = %s
           AND application_id = %s
        """,
        (finding_id, application_id),
    )
    return await row.fetchone()


async def insert_finding_disposition(
    conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    assessment_id: UUID,
    verification_finding_id: UUID,
    action: str,
    justification: str,
    created_by_user_id: UUID,
) -> dict[str, Any]:
    row = await conn.execute(
        f"""
        INSERT INTO finding_dispositions (
            application_id,
            assessment_id,
            verification_finding_id,
            action,
            justification,
            created_by_user_id
        ) VALUES (
            %s, %s, %s, %s::finding_disposition_action, %s, %s
        )
        RETURNING {_DISPOSITION_COLUMNS}
        """,
        (
            application_id,
            assessment_id,
            verification_finding_id,
            action,
            justification,
            created_by_user_id,
        ),
    )
    result = await row.fetchone()
    assert result is not None
    return result


async def fetch_extracted_field(
    conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    extracted_field_id: UUID,
) -> dict[str, Any] | None:
    row = await conn.execute(
        """
        SELECT id, application_id, extraction_id, field_name::text AS field_name
          FROM document_extracted_fields
         WHERE id = %s
           AND application_id = %s
        """,
        (extracted_field_id, application_id),
    )
    return await row.fetchone()


async def insert_field_correction(
    conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    assessment_id: UUID,
    extracted_field_id: UUID,
    corrected_value_text: str | None,
    corrected_value_numeric: Decimal | None,
    corrected_value_date: date | None,
    reason: str,
    corrected_by_user_id: UUID,
) -> dict[str, Any]:
    row = await conn.execute(
        f"""
        INSERT INTO field_corrections (
            application_id,
            extracted_field_id,
            assessment_id,
            corrected_value_text,
            corrected_value_numeric,
            corrected_value_date,
            reason,
            corrected_by_user_id
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s
        )
        RETURNING {_CORRECTION_COLUMNS}
        """,
        (
            application_id,
            extracted_field_id,
            assessment_id,
            corrected_value_text,
            corrected_value_numeric,
            corrected_value_date,
            reason,
            corrected_by_user_id,
        ),
    )
    result = await row.fetchone()
    assert result is not None
    return result
