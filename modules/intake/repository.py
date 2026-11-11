"""Raw SQL access for applications, applicants, documents, and state history (no ORM)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

_APPLICATION_COLUMNS = """
    id,
    reference_code,
    status::text AS status,
    created_by_user_id,
    assigned_reviewer_user_id,
    applicant_type::text AS applicant_type,
    requested_amount,
    currency,
    tenure_months,
    loan_purpose,
    loan_purpose_other,
    applicable_policy_version_id,
    withdraw_reason,
    submitted_at,
    archived_at,
    created_at,
    updated_at
"""

_APPLICANT_COLUMNS = """
    id,
    application_id,
    full_name,
    date_of_birth,
    pan,
    current_address,
    employer_or_business_name,
    time_in_job_or_business_months,
    gross_monthly_income,
    net_monthly_income,
    other_monthly_income,
    other_monthly_income_evidenced,
    monthly_household_expenses,
    total_existing_monthly_emis,
    outstanding_existing_loan_amount,
    created_at,
    updated_at
"""


async def insert_application(
    conn: AsyncConnection[Any],
    *,
    reference_code: str,
    created_by_user_id: UUID,
    applicant_type: str,
    requested_amount: Decimal,
    tenure_months: int,
    loan_purpose: str,
    loan_purpose_other: str | None,
    created_at: datetime,
    updated_at: datetime,
) -> dict[str, Any]:
    row = await conn.execute(
        f"""
        INSERT INTO applications (
            reference_code,
            status,
            created_by_user_id,
            applicant_type,
            requested_amount,
            currency,
            tenure_months,
            loan_purpose,
            loan_purpose_other,
            created_at,
            updated_at
        ) VALUES (
            %s,
            'draft'::application_status,
            %s,
            %s::applicant_type,
            %s,
            'INR',
            %s,
            %s,
            %s,
            %s,
            %s
        )
        RETURNING {_APPLICATION_COLUMNS}
        """,
        (
            reference_code,
            created_by_user_id,
            applicant_type,
            requested_amount,
            tenure_months,
            loan_purpose,
            loan_purpose_other,
            created_at,
            updated_at,
        ),
    )
    result = await row.fetchone()
    assert result is not None
    return result


async def insert_applicant(
    conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    full_name: str,
    date_of_birth: date,
    pan: str,
    current_address: str,
    employer_or_business_name: str,
    time_in_job_or_business_months: int,
    gross_monthly_income: Decimal,
    net_monthly_income: Decimal,
    total_existing_monthly_emis: Decimal,
    other_monthly_income: Decimal | None,
    other_monthly_income_evidenced: bool,
    monthly_household_expenses: Decimal | None,
    outstanding_existing_loan_amount: Decimal | None,
    created_at: datetime,
    updated_at: datetime,
) -> dict[str, Any]:
    row = await conn.execute(
        f"""
        INSERT INTO applicants (
            application_id,
            full_name,
            date_of_birth,
            pan,
            current_address,
            employer_or_business_name,
            time_in_job_or_business_months,
            gross_monthly_income,
            net_monthly_income,
            other_monthly_income,
            other_monthly_income_evidenced,
            monthly_household_expenses,
            total_existing_monthly_emis,
            outstanding_existing_loan_amount,
            created_at,
            updated_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        RETURNING {_APPLICANT_COLUMNS}
        """,
        (
            application_id,
            full_name,
            date_of_birth,
            pan,
            current_address,
            employer_or_business_name,
            time_in_job_or_business_months,
            gross_monthly_income,
            net_monthly_income,
            other_monthly_income,
            other_monthly_income_evidenced,
            monthly_household_expenses,
            total_existing_monthly_emis,
            outstanding_existing_loan_amount,
            created_at,
            updated_at,
        ),
    )
    result = await row.fetchone()
    assert result is not None
    return result


async def insert_state_history(
    conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    from_status: str | None,
    to_status: str,
    actor_user_id: UUID,
    created_at: datetime,
    reason: str | None = None,
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
            actor_type::text AS actor_type,
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


async def fetch_application_by_id(
    conn: AsyncConnection[Any],
    application_id: UUID,
) -> dict[str, Any] | None:
    row = await conn.execute(
        f"""
        SELECT {_APPLICATION_COLUMNS}
        FROM applications
        WHERE id = %s
        """,
        (application_id,),
    )
    return await row.fetchone()


async def lock_application_for_update(
    conn: AsyncConnection[Any],
    application_id: UUID,
) -> dict[str, Any] | None:
    """Lock the applications row for a state transition (Architecture §5)."""
    row = await conn.execute(
        f"""
        SELECT {_APPLICATION_COLUMNS}
        FROM applications
        WHERE id = %s
        FOR UPDATE
        """,
        (application_id,),
    )
    return await row.fetchone()


async def archive_application(
    conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    withdraw_reason: str,
    archived_at: datetime,
    updated_at: datetime,
) -> dict[str, Any] | None:
    row = await conn.execute(
        f"""
        UPDATE applications
        SET status = 'archived'::application_status,
            withdraw_reason = %s,
            archived_at = %s,
            updated_at = %s
        WHERE id = %s
        RETURNING {_APPLICATION_COLUMNS}
        """,
        (withdraw_reason, archived_at, updated_at, application_id),
    )
    return await row.fetchone()


async def fetch_applicant_by_application_id(
    conn: AsyncConnection[Any],
    application_id: UUID,
) -> dict[str, Any] | None:
    row = await conn.execute(
        f"""
        SELECT {_APPLICANT_COLUMNS}
        FROM applicants
        WHERE application_id = %s
        """,
        (application_id,),
    )
    return await row.fetchone()


async def list_applications(
    conn: AsyncConnection[Any],
    *,
    offset: int,
    limit: int,
    status: str | None,
) -> tuple[list[dict[str, Any]], int]:
    if status is None:
        count_row = await conn.execute(
            "SELECT COUNT(*)::int AS total FROM applications"
        )
        count_result = await count_row.fetchone()
        assert count_result is not None
        total = int(count_result["total"])

        rows = await conn.execute(
            f"""
            SELECT {_APPLICATION_COLUMNS}
            FROM applications
            ORDER BY created_at DESC, id DESC
            OFFSET %s
            LIMIT %s
            """,
            (offset, limit),
        )
    else:
        count_row = await conn.execute(
            """
            SELECT COUNT(*)::int AS total
            FROM applications
            WHERE status = %s::application_status
            """,
            (status,),
        )
        count_result = await count_row.fetchone()
        assert count_result is not None
        total = int(count_result["total"])

        rows = await conn.execute(
            f"""
            SELECT {_APPLICATION_COLUMNS}
            FROM applications
            WHERE status = %s::application_status
            ORDER BY created_at DESC, id DESC
            OFFSET %s
            LIMIT %s
            """,
            (status, offset, limit),
        )

    items = await rows.fetchall()
    return list(items), total


async def fetch_applicants_by_application_ids(
    conn: AsyncConnection[Any],
    application_ids: list[UUID],
) -> dict[UUID, dict[str, Any]]:
    if not application_ids:
        return {}
    rows = await conn.execute(
        f"""
        SELECT {_APPLICANT_COLUMNS}
        FROM applicants
        WHERE application_id = ANY(%s)
        """,
        (application_ids,),
    )
    items = await rows.fetchall()
    return {row["application_id"]: row for row in items}


async def update_application(
    conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    applicant_type: str | None,
    requested_amount: Decimal | None,
    tenure_months: int | None,
    loan_purpose: str | None,
    loan_purpose_other: str | None,
    clear_loan_purpose_other: bool,
    updated_at: datetime,
) -> dict[str, Any] | None:
    sets: list[str] = ["updated_at = %s"]
    params: list[Any] = [updated_at]

    if applicant_type is not None:
        sets.append("applicant_type = %s::applicant_type")
        params.append(applicant_type)
    if requested_amount is not None:
        sets.append("requested_amount = %s")
        params.append(requested_amount)
    if tenure_months is not None:
        sets.append("tenure_months = %s")
        params.append(tenure_months)
    if loan_purpose is not None:
        sets.append("loan_purpose = %s")
        params.append(loan_purpose)
    if clear_loan_purpose_other:
        sets.append("loan_purpose_other = NULL")
    elif loan_purpose_other is not None:
        sets.append("loan_purpose_other = %s")
        params.append(loan_purpose_other)

    params.append(application_id)
    row = await conn.execute(
        f"""
        UPDATE applications
        SET {", ".join(sets)}
        WHERE id = %s
        RETURNING {_APPLICATION_COLUMNS}
        """,
        tuple(params),
    )
    return await row.fetchone()


async def update_applicant(
    conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    fields: dict[str, Any],
    updated_at: datetime,
) -> dict[str, Any] | None:
    if not fields:
        return await fetch_applicant_by_application_id(conn, application_id)

    allowed = {
        "full_name",
        "date_of_birth",
        "pan",
        "current_address",
        "employer_or_business_name",
        "time_in_job_or_business_months",
        "gross_monthly_income",
        "net_monthly_income",
        "other_monthly_income",
        "other_monthly_income_evidenced",
        "monthly_household_expenses",
        "total_existing_monthly_emis",
        "outstanding_existing_loan_amount",
    }
    sets: list[str] = ["updated_at = %s"]
    params: list[Any] = [updated_at]
    for key, value in fields.items():
        if key not in allowed:
            continue
        sets.append(f"{key} = %s")
        params.append(value)
    params.append(application_id)

    row = await conn.execute(
        f"""
        UPDATE applicants
        SET {", ".join(sets)}
        WHERE application_id = %s
        RETURNING {_APPLICANT_COLUMNS}
        """,
        tuple(params),
    )
    return await row.fetchone()


_STORED_FILE_COLUMNS = """
    id,
    content_sha256,
    storage_uri,
    original_filename,
    mime_type,
    byte_size,
    signature_ok,
    malware_scanner,
    malware_scanned_at,
    malware_signature_version,
    created_at
"""

_APPLICATION_DOCUMENT_COLUMNS = """
    id,
    application_id,
    stored_file_id,
    content_sha256,
    document_type::text AS document_type,
    status::text AS status,
    is_alternative_evidence,
    classification_confidence,
    classification_method,
    replaced_by_document_id,
    uploaded_by_user_id,
    created_at,
    updated_at
"""


async def fetch_active_document_by_hash(
    conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    content_sha256: str,
) -> dict[str, Any] | None:
    """Active = not replaced and no replacement pointer (partial unique index)."""
    row = await conn.execute(
        f"""
        SELECT {_APPLICATION_DOCUMENT_COLUMNS}
        FROM application_documents
        WHERE application_id = %s
          AND content_sha256 = %s
          AND status <> 'replaced'::document_status
          AND replaced_by_document_id IS NULL
        """,
        (application_id, content_sha256),
    )
    return await row.fetchone()


async def insert_stored_file_if_absent(
    conn: AsyncConnection[Any],
    *,
    content_sha256: str,
    storage_uri: str,
    original_filename: str,
    mime_type: str,
    byte_size: int,
    signature_ok: bool,
    malware_scanner: str,
    malware_scanned_at: datetime,
    malware_signature_version: str | None,
    created_at: datetime,
) -> dict[str, Any]:
    """
    Insert stored_files; concurrent identical bytes use ON CONFLICT DO NOTHING
    then read the existing row (Database Design §4.3).
    """
    inserted = await conn.execute(
        f"""
        INSERT INTO stored_files (
            content_sha256,
            storage_uri,
            original_filename,
            mime_type,
            byte_size,
            signature_ok,
            malware_scanner,
            malware_scanned_at,
            malware_signature_version,
            created_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        ON CONFLICT (content_sha256) DO NOTHING
        RETURNING {_STORED_FILE_COLUMNS}
        """,
        (
            content_sha256,
            storage_uri,
            original_filename,
            mime_type,
            byte_size,
            signature_ok,
            malware_scanner,
            malware_scanned_at,
            malware_signature_version,
            created_at,
        ),
    )
    row = await inserted.fetchone()
    if row is not None:
        return row

    existing = await conn.execute(
        f"""
        SELECT {_STORED_FILE_COLUMNS}
        FROM stored_files
        WHERE content_sha256 = %s
        """,
        (content_sha256,),
    )
    result = await existing.fetchone()
    assert result is not None
    return result


async def insert_application_document(
    conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    stored_file_id: UUID,
    content_sha256: str,
    document_type: str,
    uploaded_by_user_id: UUID,
    created_at: datetime,
    updated_at: datetime,
    status: str = "uploaded",
    is_alternative_evidence: bool = False,
) -> dict[str, Any]:
    row = await conn.execute(
        f"""
        INSERT INTO application_documents (
            application_id,
            stored_file_id,
            content_sha256,
            document_type,
            status,
            is_alternative_evidence,
            uploaded_by_user_id,
            created_at,
            updated_at
        ) VALUES (
            %s,
            %s,
            %s,
            %s::document_type,
            %s::document_status,
            %s,
            %s,
            %s,
            %s
        )
        RETURNING {_APPLICATION_DOCUMENT_COLUMNS}
        """,
        (
            application_id,
            stored_file_id,
            content_sha256,
            document_type,
            status,
            is_alternative_evidence,
            uploaded_by_user_id,
            created_at,
            updated_at,
        ),
    )
    result = await row.fetchone()
    assert result is not None
    return result


async def count_documents_for_application(
    conn: AsyncConnection[Any],
    application_id: UUID,
) -> int:
    row = await conn.execute(
        """
        SELECT COUNT(*)::int AS total
        FROM application_documents
        WHERE application_id = %s
        """,
        (application_id,),
    )
    result = await row.fetchone()
    assert result is not None
    return int(result["total"])


async def count_stored_files_by_hash(
    conn: AsyncConnection[Any],
    content_sha256: str,
) -> int:
    row = await conn.execute(
        """
        SELECT COUNT(*)::int AS total
        FROM stored_files
        WHERE content_sha256 = %s
        """,
        (content_sha256,),
    )
    result = await row.fetchone()
    assert result is not None
    return int(result["total"])


_DOCUMENT_WITH_FILE_COLUMNS = f"""
    d.id,
    d.application_id,
    d.stored_file_id,
    d.content_sha256,
    d.document_type::text AS document_type,
    d.status::text AS status,
    d.is_alternative_evidence,
    d.classification_confidence,
    d.classification_method,
    d.replaced_by_document_id,
    d.uploaded_by_user_id,
    d.created_at,
    d.updated_at,
    f.storage_uri,
    f.original_filename,
    f.mime_type,
    f.byte_size,
    f.malware_scanned_at
"""


async def list_non_replaced_documents_with_files(
    conn: AsyncConnection[Any],
    application_id: UUID,
) -> list[dict[str, Any]]:
    """Active (non-replaced) documents joined to stored_files for API metadata."""
    rows = await conn.execute(
        f"""
        SELECT {_DOCUMENT_WITH_FILE_COLUMNS}
        FROM application_documents AS d
        INNER JOIN stored_files AS f ON f.id = d.stored_file_id
        WHERE d.application_id = %s
          AND d.status <> 'replaced'::document_status
        ORDER BY d.created_at ASC, d.id ASC
        """,
        (application_id,),
    )
    return list(await rows.fetchall())


async def fetch_document_with_file(
    conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    document_id: UUID,
) -> dict[str, Any] | None:
    """
    Document + stored_files row only when document_id belongs to application_id.

    Includes storage_uri for internal streaming; callers must never return it.
    """
    row = await conn.execute(
        f"""
        SELECT {_DOCUMENT_WITH_FILE_COLUMNS}
        FROM application_documents AS d
        INNER JOIN stored_files AS f ON f.id = d.stored_file_id
        WHERE d.id = %s
          AND d.application_id = %s
        """,
        (document_id, application_id),
    )
    return await row.fetchone()


_SNAPSHOT_COLUMNS = """
    id,
    application_id,
    snapshot_number,
    snapshot_hash,
    as_of_date,
    applicant_type::text AS applicant_type,
    requested_amount,
    tenure_months,
    loan_purpose,
    declared_gross_monthly_income,
    declared_net_monthly_income,
    declared_existing_emis,
    declared_other_income,
    declared_household_expenses,
    document_inventory_json,
    applicable_policy_version_id,
    created_by_user_id,
    created_at
"""

_CALC_INPUT_COLUMNS = """
    id,
    application_id,
    input_snapshot_id,
    monthly_gross_income,
    monthly_net_income,
    monthly_obligations,
    requested_loan_amount,
    tenure_months,
    annual_interest_rate_pct,
    max_allowed_foir_pct,
    created_at
"""


async def next_snapshot_number(
    conn: AsyncConnection[Any],
    application_id: UUID,
) -> int:
    """Return the next monotonic snapshot_number (>= 1) for the application."""
    row = await conn.execute(
        """
        SELECT COALESCE(MAX(snapshot_number), 0)::int AS max_number
          FROM application_input_snapshots
         WHERE application_id = %s
        """,
        (application_id,),
    )
    result = await row.fetchone()
    assert result is not None
    return int(result["max_number"]) + 1


async def insert_application_input_snapshot(
    conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    snapshot_number: int,
    snapshot_hash: str,
    as_of_date: date,
    applicant_type: str,
    requested_amount: Decimal,
    tenure_months: int,
    loan_purpose: str,
    declared_gross_monthly_income: Decimal,
    declared_net_monthly_income: Decimal,
    declared_existing_emis: Decimal,
    declared_other_income: Decimal | None,
    declared_household_expenses: Decimal | None,
    document_inventory_json: list[dict[str, Any]],
    applicable_policy_version_id: UUID,
    created_by_user_id: UUID,
    created_at: datetime,
) -> dict[str, Any]:
    row = await conn.execute(
        f"""
        INSERT INTO application_input_snapshots (
            application_id,
            snapshot_number,
            snapshot_hash,
            as_of_date,
            applicant_type,
            requested_amount,
            tenure_months,
            loan_purpose,
            declared_gross_monthly_income,
            declared_net_monthly_income,
            declared_existing_emis,
            declared_other_income,
            declared_household_expenses,
            document_inventory_json,
            applicable_policy_version_id,
            created_by_user_id,
            created_at
        ) VALUES (
            %s, %s, %s, %s,
            %s::applicant_type,
            %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s,
            %s, %s, %s
        )
        RETURNING {_SNAPSHOT_COLUMNS}
        """,
        (
            application_id,
            snapshot_number,
            snapshot_hash,
            as_of_date,
            applicant_type,
            requested_amount,
            tenure_months,
            loan_purpose,
            declared_gross_monthly_income,
            declared_net_monthly_income,
            declared_existing_emis,
            declared_other_income,
            declared_household_expenses,
            Jsonb(document_inventory_json),
            applicable_policy_version_id,
            created_by_user_id,
            created_at,
        ),
    )
    result = await row.fetchone()
    assert result is not None
    return result


async def insert_snapshot_calculation_inputs(
    conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    input_snapshot_id: UUID,
    monthly_gross_income: Decimal,
    monthly_net_income: Decimal,
    monthly_obligations: Decimal,
    requested_loan_amount: Decimal,
    tenure_months: int,
    annual_interest_rate_pct: Decimal,
    max_allowed_foir_pct: Decimal,
    created_at: datetime,
) -> dict[str, Any]:
    row = await conn.execute(
        f"""
        INSERT INTO snapshot_calculation_inputs (
            application_id,
            input_snapshot_id,
            monthly_gross_income,
            monthly_net_income,
            monthly_obligations,
            requested_loan_amount,
            tenure_months,
            annual_interest_rate_pct,
            max_allowed_foir_pct,
            created_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        RETURNING {_CALC_INPUT_COLUMNS}
        """,
        (
            application_id,
            input_snapshot_id,
            monthly_gross_income,
            monthly_net_income,
            monthly_obligations,
            requested_loan_amount,
            tenure_months,
            annual_interest_rate_pct,
            max_allowed_foir_pct,
            created_at,
        ),
    )
    result = await row.fetchone()
    assert result is not None
    return result


async def mark_application_submitted(
    conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    applicable_policy_version_id: UUID,
    updated_at: datetime,
) -> dict[str, Any] | None:
    """
    Transition draft → submitted and freeze the applicable policy version.

    submitted_at is set by applications_status_edge_before when NULL.
    """
    row = await conn.execute(
        f"""
        UPDATE applications
           SET status = 'submitted'::application_status,
               applicable_policy_version_id = %s,
               updated_at = %s
         WHERE id = %s
        RETURNING {_APPLICATION_COLUMNS}
        """,
        (applicable_policy_version_id, updated_at, application_id),
    )
    return await row.fetchone()
