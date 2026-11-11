"""Raw SQL persistence for document extractions (no SQLAlchemy / ORM)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from modules.extraction.schemas import (
    BankStatementMonthlySummary,
    ExtractedFieldResult,
    SalarySlipExtraction,
)

_EXTRACTION_COLUMNS = """
    id,
    application_id,
    application_document_id,
    processing_run_id,
    extractor_name,
    extractor_version,
    status::text AS status,
    error_code,
    error_message,
    raw_payload_json,
    created_at,
    updated_at
"""


async def insert_document_extraction(
    connection: Any,
    *,
    application_id: UUID,
    application_document_id: UUID,
    processing_run_id: UUID,
    extractor_name: str,
    extractor_version: str,
    status: str,
    created_at: datetime,
    updated_at: datetime,
    error_code: str | None = None,
    error_message: str | None = None,
    raw_payload_json: dict[str, object] | None = None,
) -> dict[str, Any]:
    row = await connection.execute(
        f"""
        INSERT INTO document_extractions (
            application_id,
            application_document_id,
            processing_run_id,
            extractor_name,
            extractor_version,
            status,
            error_code,
            error_message,
            raw_payload_json,
            created_at,
            updated_at
        ) VALUES (
            %s, %s, %s, %s, %s,
            %s::extraction_status,
            %s, %s, %s, %s, %s
        )
        RETURNING {_EXTRACTION_COLUMNS}
        """,
        (
            application_id,
            application_document_id,
            processing_run_id,
            extractor_name,
            extractor_version,
            status,
            error_code,
            error_message,
            Jsonb(raw_payload_json or {}),
            created_at,
            updated_at,
        ),
    )
    result = await row.fetchone()
    assert result is not None
    return dict(result)


async def update_document_extraction_status(
    connection: Any,
    *,
    extraction_id: UUID,
    application_id: UUID,
    status: str,
    updated_at: datetime,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    await connection.execute(
        """
        UPDATE document_extractions
           SET status = %s::extraction_status,
               error_code = %s,
               error_message = %s,
               updated_at = %s
         WHERE id = %s
           AND application_id = %s
        """,
        (
            status,
            error_code,
            error_message,
            updated_at,
            extraction_id,
            application_id,
        ),
    )


async def insert_extracted_field(
    connection: Any,
    *,
    application_id: UUID,
    extraction_id: UUID,
    field: ExtractedFieldResult,
    created_at: datetime,
) -> dict[str, Any]:
    bbox = field.bbox
    row = await connection.execute(
        """
        INSERT INTO document_extracted_fields (
            application_id,
            extraction_id,
            field_name,
            raw_value,
            normalized_value_json,
            confidence,
            page_number,
            bbox_x_min,
            bbox_y_min,
            bbox_x_max,
            bbox_y_max,
            created_at
        ) VALUES (
            %s, %s, %s::extraction_field_name, %s, %s, %s, %s,
            %s, %s, %s, %s, %s
        )
        RETURNING
            id,
            field_name::text AS field_name,
            raw_value,
            normalized_value_json,
            confidence,
            page_number,
            bbox_x_min,
            bbox_y_min,
            bbox_x_max,
            bbox_y_max
        """,
        (
            application_id,
            extraction_id,
            field.field_name,
            field.raw_value,
            Jsonb(field.normalized_value) if field.normalized_value is not None else None,
            field.confidence,
            field.page_number,
            None if bbox is None else bbox[0],
            None if bbox is None else bbox[1],
            None if bbox is None else bbox[2],
            None if bbox is None else bbox[3],
            created_at,
        ),
    )
    result = await row.fetchone()
    assert result is not None
    return dict(result)


async def insert_salary_slip_extraction(
    connection: Any,
    *,
    application_id: UUID,
    extraction_id: UUID,
    data: SalarySlipExtraction,
    created_at: datetime,
) -> dict[str, Any]:
    row = await connection.execute(
        """
        INSERT INTO salary_slip_extractions (
            application_id,
            extraction_id,
            pay_period_month,
            basic_pay,
            allowances,
            deductions,
            net_pay,
            employer_name,
            employee_pan,
            created_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        RETURNING
            id,
            pay_period_month,
            basic_pay,
            allowances,
            deductions,
            net_pay,
            employer_name,
            employee_pan
        """,
        (
            application_id,
            extraction_id,
            data.pay_period_month,
            data.basic_pay,
            data.allowances,
            data.deductions,
            data.net_pay,
            data.employer_name,
            data.employee_pan,
            created_at,
        ),
    )
    result = await row.fetchone()
    assert result is not None
    return dict(result)


async def insert_bank_statement_monthly_summary(
    connection: Any,
    *,
    application_id: UUID,
    extraction_id: UUID,
    summary: BankStatementMonthlySummary,
    created_at: datetime,
) -> dict[str, Any]:
    row = await connection.execute(
        """
        INSERT INTO bank_statement_monthly_summary (
            application_id,
            extraction_id,
            statement_month,
            total_credits,
            total_debits,
            closing_balance,
            identified_salary_credits,
            salary_credit_count,
            created_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        RETURNING
            id,
            statement_month,
            total_credits,
            total_debits,
            closing_balance,
            identified_salary_credits,
            salary_credit_count
        """,
        (
            application_id,
            extraction_id,
            summary.statement_month,
            summary.total_credits,
            summary.total_debits,
            summary.closing_balance,
            summary.identified_salary_credits,
            summary.salary_credit_count,
            created_at,
        ),
    )
    result = await row.fetchone()
    assert result is not None
    return dict(result)


async def update_application_document_status(
    connection: Any,
    *,
    application_id: UUID,
    application_document_id: UUID,
    status: str,
    updated_at: datetime,
) -> None:
    await connection.execute(
        """
        UPDATE application_documents
           SET status = %s::document_status,
               updated_at = %s
         WHERE id = %s
           AND application_id = %s
        """,
        (status, updated_at, application_document_id, application_id),
    )


async def insert_extraction_failure_event(
    connection: Any,
    *,
    application_id: UUID,
    application_document_id: UUID,
    processing_run_id: UUID,
    claim_fence: int,
    reason: str,
    created_at: datetime,
) -> None:
    await connection.execute(
        """
        INSERT INTO document_failure_events (
            application_id,
            application_document_id,
            processing_run_id,
            kind,
            affected_pages_json,
            reason,
            actor_type,
            actor_user_id,
            claim_fence,
            created_at
        ) VALUES (
            %s,
            %s,
            %s,
            'extraction_failed'::document_failure_event_kind,
            %s,
            %s,
            'system'::audit_actor_type,
            NULL,
            %s,
            %s
        )
        ON CONFLICT (processing_run_id, application_document_id, kind)
        DO NOTHING
        """,
        (
            application_id,
            application_document_id,
            processing_run_id,
            Jsonb([]),
            reason,
            claim_fence,
            created_at,
        ),
    )


async def get_extraction_by_document_run(
    connection: Any,
    *,
    application_document_id: UUID,
    processing_run_id: UUID,
) -> dict[str, Any] | None:
    row = await connection.execute(
        f"""
        SELECT {_EXTRACTION_COLUMNS}
          FROM document_extractions
         WHERE application_document_id = %s
           AND processing_run_id = %s
        """,
        (application_document_id, processing_run_id),
    )
    result = await row.fetchone()
    return dict(result) if result is not None else None
