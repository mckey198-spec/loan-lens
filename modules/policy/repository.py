"""Raw SQL access for policy documents, versions, thresholds, requirements, and chunks."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

_POLICY_DOCUMENT_COLUMNS = """
    id,
    title,
    lender_label,
    created_by_user_id,
    created_at
"""

_POLICY_VERSION_COLUMNS = """
    id,
    policy_document_id,
    version_label,
    status::text AS status,
    annual_interest_rate_pct,
    effective_from,
    effective_to,
    created_by_user_id,
    created_at,
    updated_at
"""

_THRESHOLD_COLUMNS = """
    id,
    policy_version_id,
    applicant_type::text AS applicant_type,
    min_monthly_income,
    max_foir_pct,
    min_employment_months,
    max_loan_amount,
    created_at
"""

_REQUIREMENT_COLUMNS = """
    id,
    policy_version_id,
    applicant_type::text AS applicant_type,
    document_type::text AS document_type,
    is_mandatory,
    created_at
"""

_CHUNK_COLUMNS = """
    id,
    policy_version_id,
    chunk_index,
    section_label,
    content_text,
    created_at
"""


async def insert_policy_document(
    conn: AsyncConnection[Any],
    *,
    title: str,
    lender_label: str,
    created_by_user_id: UUID,
    created_at: datetime,
) -> dict[str, Any]:
    row = await conn.execute(
        f"""
        INSERT INTO policy_documents (
            title,
            lender_label,
            created_by_user_id,
            created_at
        ) VALUES (%s, %s, %s, %s)
        RETURNING {_POLICY_DOCUMENT_COLUMNS}
        """,
        (title, lender_label, created_by_user_id, created_at),
    )
    result = await row.fetchone()
    assert result is not None
    return result


async def fetch_policy_document(
    conn: AsyncConnection[Any],
    *,
    policy_document_id: UUID,
) -> dict[str, Any] | None:
    row = await conn.execute(
        f"""
        SELECT {_POLICY_DOCUMENT_COLUMNS}
          FROM policy_documents
         WHERE id = %s
        """,
        (policy_document_id,),
    )
    return await row.fetchone()


async def insert_policy_version(
    conn: AsyncConnection[Any],
    *,
    policy_document_id: UUID,
    version_label: str,
    annual_interest_rate_pct: Decimal,
    effective_from: date,
    effective_to: date | None,
    created_by_user_id: UUID,
    created_at: datetime,
    updated_at: datetime,
) -> dict[str, Any]:
    row = await conn.execute(
        f"""
        INSERT INTO policy_versions (
            policy_document_id,
            version_label,
            status,
            annual_interest_rate_pct,
            effective_from,
            effective_to,
            created_by_user_id,
            created_at,
            updated_at
        ) VALUES (
            %s,
            %s,
            'draft'::policy_status,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s
        )
        RETURNING {_POLICY_VERSION_COLUMNS}
        """,
        (
            policy_document_id,
            version_label,
            annual_interest_rate_pct,
            effective_from,
            effective_to,
            created_by_user_id,
            created_at,
            updated_at,
        ),
    )
    result = await row.fetchone()
    assert result is not None
    return result


async def fetch_policy_version(
    conn: AsyncConnection[Any],
    *,
    policy_version_id: UUID,
) -> dict[str, Any] | None:
    row = await conn.execute(
        f"""
        SELECT {_POLICY_VERSION_COLUMNS}
          FROM policy_versions
         WHERE id = %s
        """,
        (policy_version_id,),
    )
    return await row.fetchone()


async def insert_threshold_config(
    conn: AsyncConnection[Any],
    *,
    policy_version_id: UUID,
    applicant_type: str,
    min_monthly_income: Decimal,
    max_foir_pct: Decimal,
    min_employment_months: int,
    max_loan_amount: Decimal,
    created_at: datetime,
) -> dict[str, Any]:
    row = await conn.execute(
        f"""
        INSERT INTO policy_threshold_configs (
            policy_version_id,
            applicant_type,
            min_monthly_income,
            max_foir_pct,
            min_employment_months,
            max_loan_amount,
            created_at
        ) VALUES (
            %s,
            %s::applicant_type,
            %s,
            %s,
            %s,
            %s,
            %s
        )
        RETURNING {_THRESHOLD_COLUMNS}
        """,
        (
            policy_version_id,
            applicant_type,
            min_monthly_income,
            max_foir_pct,
            min_employment_months,
            max_loan_amount,
            created_at,
        ),
    )
    result = await row.fetchone()
    assert result is not None
    return result


async def insert_document_requirement(
    conn: AsyncConnection[Any],
    *,
    policy_version_id: UUID,
    applicant_type: str,
    document_type: str,
    is_mandatory: bool,
    created_at: datetime,
) -> dict[str, Any]:
    row = await conn.execute(
        f"""
        INSERT INTO policy_document_requirements (
            policy_version_id,
            applicant_type,
            document_type,
            is_mandatory,
            created_at
        ) VALUES (
            %s,
            %s::applicant_type,
            %s::document_type,
            %s,
            %s
        )
        RETURNING {_REQUIREMENT_COLUMNS}
        """,
        (
            policy_version_id,
            applicant_type,
            document_type,
            is_mandatory,
            created_at,
        ),
    )
    result = await row.fetchone()
    assert result is not None
    return result


async def delete_chunks_for_version(
    conn: AsyncConnection[Any],
    *,
    policy_version_id: UUID,
) -> None:
    await conn.execute(
        "DELETE FROM policy_chunks WHERE policy_version_id = %s",
        (policy_version_id,),
    )


async def insert_policy_chunk(
    conn: AsyncConnection[Any],
    *,
    policy_version_id: UUID,
    chunk_index: int,
    section_label: str,
    content_text: str,
    created_at: datetime,
) -> dict[str, Any]:
    row = await conn.execute(
        f"""
        INSERT INTO policy_chunks (
            policy_version_id,
            chunk_index,
            section_label,
            content_text,
            created_at
        ) VALUES (%s, %s, %s, %s, %s)
        RETURNING {_CHUNK_COLUMNS}
        """,
        (
            policy_version_id,
            chunk_index,
            section_label,
            content_text,
            created_at,
        ),
    )
    result = await row.fetchone()
    assert result is not None
    return result


async def list_chunks_for_version(
    conn: AsyncConnection[Any],
    *,
    policy_version_id: UUID,
) -> list[dict[str, Any]]:
    rows = await conn.execute(
        f"""
        SELECT {_CHUNK_COLUMNS}
          FROM policy_chunks
         WHERE policy_version_id = %s
         ORDER BY chunk_index ASC
        """,
        (policy_version_id,),
    )
    return list(await rows.fetchall())


async def activate_policy_version(
    conn: AsyncConnection[Any],
    *,
    policy_version_id: UUID,
    updated_at: datetime,
) -> dict[str, Any]:
    row = await conn.execute(
        f"""
        UPDATE policy_versions
           SET status = 'active'::policy_status,
               updated_at = %s
         WHERE id = %s
           AND status = 'draft'::policy_status
        RETURNING {_POLICY_VERSION_COLUMNS}
        """,
        (updated_at, policy_version_id),
    )
    result = await row.fetchone()
    return result


async def list_active_policy_versions_for_date(
    conn: AsyncConnection[Any],
    *,
    as_of_date: date,
) -> list[dict[str, Any]]:
    rows = await conn.execute(
        f"""
        SELECT {_POLICY_VERSION_COLUMNS}
          FROM policy_versions
         WHERE status = 'active'::policy_status
           AND daterange(effective_from, effective_to, '[]') @> %s::date
         ORDER BY effective_from ASC, id ASC
        """,
        (as_of_date,),
    )
    return list(await rows.fetchall())


async def list_threshold_configs(
    conn: AsyncConnection[Any],
    *,
    policy_version_id: UUID,
) -> list[dict[str, Any]]:
    rows = await conn.execute(
        f"""
        SELECT {_THRESHOLD_COLUMNS}
          FROM policy_threshold_configs
         WHERE policy_version_id = %s
         ORDER BY applicant_type::text ASC
        """,
        (policy_version_id,),
    )
    return list(await rows.fetchall())


async def list_document_requirements(
    conn: AsyncConnection[Any],
    *,
    policy_version_id: UUID,
) -> list[dict[str, Any]]:
    rows = await conn.execute(
        f"""
        SELECT {_REQUIREMENT_COLUMNS}
          FROM policy_document_requirements
         WHERE policy_version_id = %s
         ORDER BY applicant_type::text ASC, document_type::text ASC
        """,
        (policy_version_id,),
    )
    return list(await rows.fetchall())
