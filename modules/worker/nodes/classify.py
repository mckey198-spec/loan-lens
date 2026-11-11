"""Classify application documents and record unsupported failures (ADR-013)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname
from uuid import UUID

from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from modules.classification.classifier import classify_document_text
from modules.classification.schemas import ClassificationResult

logger = logging.getLogger("loanlens.worker")

WorkflowState = dict[str, Any]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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
    """
    Read extractable text for classification.

    Prefer an explicit stub (tests / pre-extracted text). Otherwise attempt a
    UTF-8 decode of on-disk bytes for text-like files. Binary PDFs without a
    text layer yield empty string and classify as unsupported until OCR exists.
    """
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

    # Best-effort: some fixtures store plain text behind a PDF mime/name.
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        return ""
    if "\x00" in decoded:
        return ""
    return decoded


async def _load_active_documents(
    connection: Any,
    *,
    application_id: UUID,
    input_snapshot_id: UUID | None,
) -> list[dict[str, Any]]:
    """Active documents for the run, preferring the frozen snapshot inventory."""
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
               AND d.status <> 'replaced'::document_status
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
           AND d.status <> 'replaced'::document_status
         ORDER BY d.created_at ASC, d.id ASC
        """,
        (application_id,),
    )
    return list(await rows.fetchall())


async def _update_document_classification(
    connection: Any,
    *,
    document_id: UUID,
    application_id: UUID,
    result: ClassificationResult,
) -> None:
    status = (
        "unsupported"
        if result.document_type == "unsupported"
        else "classified"
    )
    now = _utcnow()
    await connection.execute(
        """
        UPDATE application_documents
           SET document_type = %s::document_type,
               status = %s::document_status,
               classification_confidence = %s,
               classification_method = %s,
               updated_at = %s
         WHERE id = %s
           AND application_id = %s
        """,
        (
            result.document_type,
            status,
            result.confidence,
            result.method,
            now,
            document_id,
            application_id,
        ),
    )


async def _insert_unsupported_failure(
    connection: Any,
    *,
    application_id: UUID,
    document_id: UUID,
    processing_run_id: UUID,
    claim_fence: int,
    reason: str,
) -> None:
    """Append marked_unsupported fact; idempotent on logical key (Architecture §6)."""
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
            'marked_unsupported'::document_failure_event_kind,
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
            document_id,
            processing_run_id,
            Jsonb([]),
            reason,
            claim_fence,
            _utcnow(),
        ),
    )


async def classify_documents(state: WorkflowState) -> WorkflowState:
    """
    Pipeline node: classify all active documents for the processing run.

    Requires ``application_id``, ``processing_run_id``, ``claim_fence``, and
    ``_db_pool`` (injected by the runner; never checkpointed).
    """
    pool = state.get("_db_pool")
    if not isinstance(pool, AsyncConnectionPool):
        raise ValueError("classify_documents requires _db_pool in workflow state")

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

    classifications: list[dict[str, Any]] = []

    async with pool.connection() as conn:
        async with conn.transaction():
            if input_snapshot_id is None:
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
                if run is not None and run["input_snapshot_id"] is not None:
                    input_snapshot_id = run["input_snapshot_id"]

            documents = await _load_active_documents(
                conn,
                application_id=application_id,
                input_snapshot_id=input_snapshot_id,
            )

            for document in documents:
                document_id: UUID = document["id"]
                filename = str(document["original_filename"] or "")
                stub_text = stub_map.get(str(document_id))
                text = _read_document_text(
                    storage_uri=str(document["storage_uri"]),
                    mime_type=str(document["mime_type"] or ""),
                    filename=filename,
                    stub_text=stub_text,
                )
                result = classify_document_text(text, filename)
                await _update_document_classification(
                    conn,
                    document_id=document_id,
                    application_id=application_id,
                    result=result,
                )
                if result.document_type == "unsupported":
                    reason = (
                        "Document type could not be classified with sufficient "
                        f"confidence (method={result.method}, "
                        f"confidence={result.confidence})."
                    )
                    await _insert_unsupported_failure(
                        conn,
                        application_id=application_id,
                        document_id=document_id,
                        processing_run_id=processing_run_id,
                        claim_fence=claim_fence,
                        reason=reason,
                    )

                classifications.append(
                    {
                        "document_id": str(document_id),
                        "document_type": result.document_type,
                        "confidence": str(result.confidence),
                        "method": result.method,
                        "status": (
                            "unsupported"
                            if result.document_type == "unsupported"
                            else "classified"
                        ),
                    }
                )

                logger.info(
                    "document_classified",
                    extra={
                        "application_id": str(application_id),
                        "processing_run_id": str(processing_run_id),
                        "claim_fence": claim_fence,
                        "document_id": str(document_id),
                        "document_type": result.document_type,
                        "classification_confidence": str(result.confidence),
                        "classification_method": result.method,
                    },
                )

    updated = dict(state)
    updated.pop("_db_pool", None)
    updated["classifications"] = classifications
    updated["classified_count"] = len(classifications)
    updated["unsupported_count"] = sum(
        1 for item in classifications if item["document_type"] == "unsupported"
    )
    return updated
