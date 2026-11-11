"""Document extraction service (Architecture §7; ADR-011 / layer separation)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence
from uuid import UUID

from modules.extraction import repository
from modules.extraction.schemas import (
    BankStatementMonthlySummary,
    ExtractedFieldResult,
    SalarySlipExtraction,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def record_document_extraction(
    connection: Any,
    application_id: UUID,
    application_document_id: UUID,
    processing_run_id: UUID,
    extractor_name: str,
    extractor_version: str,
    fields: Sequence[ExtractedFieldResult],
    salary_slip_data: SalarySlipExtraction | None = None,
    bank_summaries: Sequence[BankStatementMonthlySummary] | None = None,
    *,
    raw_payload_json: dict[str, object] | None = None,
) -> dict[str, Any]:
    """
    Persist a completed extraction for one document in the caller's transaction.

    - Inserts ``document_extractions`` with status ``completed``.
    - Appends key-value rows into ``document_extracted_fields`` (append-only).
    - Inserts structured ``salary_slip_extractions`` / monthly bank rows when given.
    - Sets ``application_documents.status = 'extracted'``.
    """
    now = _utcnow()
    extraction = await repository.insert_document_extraction(
        connection,
        application_id=application_id,
        application_document_id=application_document_id,
        processing_run_id=processing_run_id,
        extractor_name=extractor_name,
        extractor_version=extractor_version,
        status="completed",
        raw_payload_json=raw_payload_json,
        created_at=now,
        updated_at=now,
    )
    extraction_id: UUID = extraction["id"]

    field_rows: list[dict[str, Any]] = []
    for field in fields:
        field_rows.append(
            await repository.insert_extracted_field(
                connection,
                application_id=application_id,
                extraction_id=extraction_id,
                field=field,
                created_at=now,
            )
        )

    salary_row: dict[str, Any] | None = None
    if salary_slip_data is not None:
        salary_row = await repository.insert_salary_slip_extraction(
            connection,
            application_id=application_id,
            extraction_id=extraction_id,
            data=salary_slip_data,
            created_at=now,
        )

    bank_rows: list[dict[str, Any]] = []
    if bank_summaries:
        for summary in bank_summaries:
            bank_rows.append(
                await repository.insert_bank_statement_monthly_summary(
                    connection,
                    application_id=application_id,
                    extraction_id=extraction_id,
                    summary=summary,
                    created_at=now,
                )
            )

    await repository.update_application_document_status(
        connection,
        application_id=application_id,
        application_document_id=application_document_id,
        status="extracted",
        updated_at=now,
    )

    return {
        "extraction": extraction,
        "fields": field_rows,
        "salary_slip": salary_row,
        "bank_summaries": bank_rows,
    }


async def record_extraction_failure(
    connection: Any,
    *,
    application_id: UUID,
    application_document_id: UUID,
    processing_run_id: UUID,
    claim_fence: int,
    extractor_name: str,
    extractor_version: str,
    error_code: str,
    error_message: str,
) -> dict[str, Any]:
    """
    Mark extraction failed for one document.

    Creates/updates ``document_extractions`` to ``failed``, sets the document
    status to ``failed``, and appends ``document_failure_events`` with
    ``kind='extraction_failed'`` and the current ``claim_fence``.
    """
    now = _utcnow()
    existing = await repository.get_extraction_by_document_run(
        connection,
        application_document_id=application_document_id,
        processing_run_id=processing_run_id,
    )
    if existing is None:
        extraction = await repository.insert_document_extraction(
            connection,
            application_id=application_id,
            application_document_id=application_document_id,
            processing_run_id=processing_run_id,
            extractor_name=extractor_name,
            extractor_version=extractor_version,
            status="failed",
            error_code=error_code,
            error_message=error_message,
            raw_payload_json={"error_code": error_code},
            created_at=now,
            updated_at=now,
        )
    else:
        await repository.update_document_extraction_status(
            connection,
            extraction_id=existing["id"],
            application_id=application_id,
            status="failed",
            updated_at=now,
            error_code=error_code,
            error_message=error_message,
        )
        extraction = {
            **existing,
            "status": "failed",
            "error_code": error_code,
            "error_message": error_message,
            "updated_at": now,
        }

    await repository.update_application_document_status(
        connection,
        application_id=application_id,
        application_document_id=application_document_id,
        status="failed",
        updated_at=now,
    )
    await repository.insert_extraction_failure_event(
        connection,
        application_id=application_id,
        application_document_id=application_document_id,
        processing_run_id=processing_run_id,
        claim_fence=claim_fence,
        reason=error_message,
        created_at=now,
    )
    return extraction
