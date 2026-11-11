"""Extract structured fields from classified documents (Architecture §7)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname
from uuid import UUID

from psycopg_pool import AsyncConnectionPool

from modules.extraction.parsers import extract_document
from modules.extraction.schemas import EXTRACTOR_VERSION, ExtractionError
from modules.extraction.service import (
    record_document_extraction,
    record_extraction_failure,
)

logger = logging.getLogger("loanlens.worker")

WorkflowState = dict[str, Any]

_EXTRACTABLE_TYPES = frozenset({"pan_card", "salary_slip", "bank_statement"})


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


def _path_from_storage_uri(storage_uri: str) -> Path:
    parsed = urlparse(storage_uri)
    if parsed.scheme == "file":
        return Path(url2pathname(unquote(parsed.path)))
    return Path(storage_uri)


def _read_document_text(
    *,
    storage_uri: str,
    mime_type: str,
    filename: str,
    stub_text: str | None,
) -> str:
    if stub_text is not None:
        return stub_text

    path = _path_from_storage_uri(storage_uri)
    if not path.is_file():
        return ""

    raw = path.read_bytes()
    lowered_mime = (mime_type or "").lower()
    lowered_name = filename.casefold()
    text_like = (
        lowered_mime.startswith("text/")
        or lowered_mime in {"application/json", "application/xml"}
        or lowered_name.endswith((".txt", ".csv", ".json", ".xml"))
    )
    if text_like:
        return raw.decode("utf-8", errors="replace")

    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        return ""
    if "\x00" in decoded:
        return ""
    return decoded


async def _load_classified_documents(
    connection: Any,
    *,
    application_id: UUID,
    input_snapshot_id: UUID | None,
) -> list[dict[str, Any]]:
    """Documents in the run inventory with status ``classified``."""
    document_ids: list[UUID] | None = None
    if input_snapshot_id is not None:
        snap = await connection.execute(
            """
            SELECT document_inventory_json
              FROM application_input_snapshots
             WHERE id = %s
               AND application_id = %s
            """,
            (input_snapshot_id, application_id),
        )
        row = await snap.fetchone()
        if row is not None:
            inventory = row["document_inventory_json"] or []
            document_ids = [
                UUID(str(item["document_id"]))
                for item in inventory
                if item.get("document_id")
            ]

    if document_ids is not None:
        if not document_ids:
            return []
        rows = await connection.execute(
            """
            SELECT
                d.id,
                d.application_id,
                d.document_type::text AS document_type,
                d.status::text AS status,
                f.storage_uri,
                f.original_filename,
                f.mime_type
              FROM application_documents AS d
              INNER JOIN stored_files AS f ON f.id = d.stored_file_id
             WHERE d.application_id = %s
               AND d.id = ANY(%s)
               AND d.status = 'classified'::document_status
             ORDER BY d.created_at ASC, d.id ASC
            """,
            (application_id, document_ids),
        )
        return list(await rows.fetchall())

    rows = await connection.execute(
        """
        SELECT
            d.id,
            d.application_id,
            d.document_type::text AS document_type,
            d.status::text AS status,
            f.storage_uri,
            f.original_filename,
            f.mime_type
          FROM application_documents AS d
          INNER JOIN stored_files AS f ON f.id = d.stored_file_id
         WHERE d.application_id = %s
           AND d.status = 'classified'::document_status
         ORDER BY d.created_at ASC, d.id ASC
        """,
        (application_id,),
    )
    return list(await rows.fetchall())


async def extract_documents(state: WorkflowState) -> WorkflowState:
    """
    Pipeline node: extract fields from classified documents.

    Each document is persisted in its own transaction. Failures mark the
    document ``failed`` and append ``document_failure_events`` without aborting
    sibling documents.
    """
    pool = state.get("_db_pool")
    if not isinstance(pool, AsyncConnectionPool):
        raise ValueError("extract_documents requires _db_pool in workflow state")

    application_id = _as_uuid(state.get("application_id"), "application_id")
    processing_run_id = _as_uuid(
        state.get("processing_run_id"),
        "processing_run_id",
    )
    claim_fence = _as_fence(state.get("claim_fence"))

    input_snapshot_raw = state.get("input_snapshot_id")
    input_snapshot_id: UUID | None
    if input_snapshot_raw is None or input_snapshot_raw == "":
        input_snapshot_id = None
    else:
        input_snapshot_id = _as_uuid(input_snapshot_raw, "input_snapshot_id")

    stubs = state.get("document_texts")
    stub_map: dict[str, str] = stubs if isinstance(stubs, dict) else {}

    extractions: list[dict[str, Any]] = []

    async with pool.connection() as lookup_conn:
        if input_snapshot_id is None:
            run_row = await lookup_conn.execute(
                """
                SELECT input_snapshot_id
                  FROM processing_runs
                 WHERE id = %s
                   AND application_id = %s
                """,
                (processing_run_id, application_id),
            )
            run = await run_row.fetchone()
            if run is not None and run["input_snapshot_id"] is not None:
                input_snapshot_id = run["input_snapshot_id"]
        documents = await _load_classified_documents(
            lookup_conn,
            application_id=application_id,
            input_snapshot_id=input_snapshot_id,
        )
        await lookup_conn.commit()

    for document in documents:
        document_id: UUID = document["id"]
        document_type = str(document["document_type"])
        filename = str(document["original_filename"] or "")
        stub_text = stub_map.get(str(document_id))
        text = _read_document_text(
            storage_uri=str(document["storage_uri"]),
            mime_type=str(document["mime_type"] or ""),
            filename=filename,
            stub_text=stub_text,
        )

        if document_type not in _EXTRACTABLE_TYPES:
            extractions.append(
                {
                    "document_id": str(document_id),
                    "document_type": document_type,
                    "status": "skipped",
                    "reason": "no_extractor",
                }
            )
            continue

        async with pool.connection() as conn:
            async with conn.transaction():
                try:
                    result = extract_document(document_type, text)
                    persisted = await record_document_extraction(
                        conn,
                        application_id,
                        document_id,
                        processing_run_id,
                        result.extractor_name,
                        result.extractor_version,
                        result.fields,
                        salary_slip_data=result.salary_slip_data,
                        bank_summaries=result.bank_summaries,
                        raw_payload_json=result.raw_payload,
                    )
                    extractions.append(
                        {
                            "document_id": str(document_id),
                            "document_type": document_type,
                            "status": "extracted",
                            "extraction_id": str(persisted["extraction"]["id"]),
                            "field_count": len(persisted["fields"]),
                            "extractor_name": result.extractor_name,
                        }
                    )
                    logger.info(
                        "document_extracted",
                        extra={
                            "application_id": str(application_id),
                            "processing_run_id": str(processing_run_id),
                            "claim_fence": claim_fence,
                            "document_id": str(document_id),
                            "document_type": document_type,
                            "extractor_name": result.extractor_name,
                            "field_count": len(persisted["fields"]),
                        },
                    )
                except ExtractionError as exc:
                    await record_extraction_failure(
                        conn,
                        application_id=application_id,
                        application_document_id=document_id,
                        processing_run_id=processing_run_id,
                        claim_fence=claim_fence,
                        extractor_name=f"{document_type}_extractor",
                        extractor_version=EXTRACTOR_VERSION,
                        error_code=exc.code,
                        error_message=exc.message,
                    )
                    extractions.append(
                        {
                            "document_id": str(document_id),
                            "document_type": document_type,
                            "status": "failed",
                            "error_code": exc.code,
                            "error_message": exc.message,
                        }
                    )
                    logger.warning(
                        "document_extraction_failed",
                        extra={
                            "application_id": str(application_id),
                            "processing_run_id": str(processing_run_id),
                            "claim_fence": claim_fence,
                            "document_id": str(document_id),
                            "document_type": document_type,
                            "error_code": exc.code,
                        },
                    )

    updated = dict(state)
    updated.pop("_db_pool", None)
    updated["extractions"] = extractions
    updated["extracted_count"] = sum(
        1 for item in extractions if item["status"] == "extracted"
    )
    updated["extraction_failed_count"] = sum(
        1 for item in extractions if item["status"] == "failed"
    )
    return updated
