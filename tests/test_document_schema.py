"""Schema tests for stored_files, application_documents, and hash triggers (M04-T01)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from psycopg import AsyncConnection
from psycopg.errors import CheckViolation, InsufficientPrivilege, UniqueViolation

from tests.conftest import _insert_test_user, _purge_user

_HASH_A = "a" * 64
_HASH_B = "b" * 64
_HASH_C = "c" * 64


async def _cleanup_application_documents(
    db_conn: AsyncConnection[Any],
    application_id: UUID,
) -> None:
    await db_conn.execute(
        "ALTER TABLE document_failure_resolutions "
        "DISABLE TRIGGER document_failure_resolutions_append_only_trg"
    )
    await db_conn.execute(
        "DELETE FROM document_failure_resolutions WHERE application_id = %s",
        (application_id,),
    )
    await db_conn.execute(
        "ALTER TABLE document_failure_resolutions "
        "ENABLE TRIGGER document_failure_resolutions_append_only_trg"
    )

    await db_conn.execute(
        "ALTER TABLE document_failure_events "
        "DISABLE TRIGGER document_failure_events_append_only_trg"
    )
    await db_conn.execute(
        "DELETE FROM document_failure_events WHERE application_id = %s",
        (application_id,),
    )
    await db_conn.execute(
        "ALTER TABLE document_failure_events "
        "ENABLE TRIGGER document_failure_events_append_only_trg"
    )

    await db_conn.execute(
        "DELETE FROM application_documents WHERE application_id = %s",
        (application_id,),
    )
    await db_conn.commit()


async def _cleanup_application(
    db_conn: AsyncConnection[Any],
    application_id: UUID,
) -> None:
    await _cleanup_application_documents(db_conn, application_id)
    await db_conn.execute(
        "ALTER TABLE application_state_history "
        "DISABLE TRIGGER application_state_history_append_only_trg"
    )
    await db_conn.execute(
        "DELETE FROM application_state_history WHERE application_id = %s",
        (application_id,),
    )
    await db_conn.execute(
        "ALTER TABLE application_state_history "
        "ENABLE TRIGGER application_state_history_append_only_trg"
    )
    await db_conn.execute(
        "DELETE FROM applicants WHERE application_id = %s",
        (application_id,),
    )
    await db_conn.execute(
        "DELETE FROM applications WHERE id = %s",
        (application_id,),
    )
    await db_conn.commit()


async def _cleanup_stored_file(
    db_conn: AsyncConnection[Any],
    stored_file_id: UUID,
) -> None:
    await db_conn.execute(
        "ALTER TABLE stored_files DISABLE TRIGGER stored_files_immutable_trg"
    )
    await db_conn.execute(
        "DELETE FROM stored_files WHERE id = %s",
        (stored_file_id,),
    )
    await db_conn.execute(
        "ALTER TABLE stored_files ENABLE TRIGGER stored_files_immutable_trg"
    )
    await db_conn.commit()


async def _insert_draft_application(
    db_conn: AsyncConnection[Any],
    *,
    created_by_user_id: UUID,
    reference_code: str | None = None,
) -> UUID:
    now = datetime.now(timezone.utc)
    ref = reference_code or f"LL-{uuid4().hex[:12].upper()}"
    row = await db_conn.execute(
        """
        INSERT INTO applications (
            reference_code,
            status,
            created_by_user_id,
            applicant_type,
            requested_amount,
            currency,
            tenure_months,
            loan_purpose,
            created_at,
            updated_at
        ) VALUES (
            %s,
            'draft'::application_status,
            %s,
            'salaried'::applicant_type,
            %s,
            'INR',
            36,
            'home_renovation',
            %s,
            %s
        )
        RETURNING id
        """,
        (ref, created_by_user_id, Decimal("500000.00"), now, now),
    )
    result = await row.fetchone()
    assert result is not None
    return result["id"]


async def _insert_stored_file(
    db_conn: AsyncConnection[Any],
    *,
    content_sha256: str,
    original_filename: str = "statement.pdf",
    mime_type: str = "application/pdf",
    byte_size: int = 1024,
) -> UUID:
    now = datetime.now(timezone.utc)
    row = await db_conn.execute(
        """
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
            %s, %s, %s, %s, %s, true, 'clamav', %s, 'daily.cvd', %s
        )
        RETURNING id
        """,
        (
            content_sha256,
            f"file:///{content_sha256[:16]}",
            original_filename,
            mime_type,
            byte_size,
            now,
            now,
        ),
    )
    result = await row.fetchone()
    assert result is not None
    return result["id"]


async def _insert_application_document(
    db_conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    stored_file_id: UUID,
    content_sha256: str,
    uploaded_by_user_id: UUID,
    document_type: str = "bank_statement",
    status: str = "uploaded",
    replaced_by_document_id: UUID | None = None,
) -> UUID:
    now = datetime.now(timezone.utc)
    row = await db_conn.execute(
        """
        INSERT INTO application_documents (
            application_id,
            stored_file_id,
            content_sha256,
            document_type,
            status,
            replaced_by_document_id,
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
        RETURNING id
        """,
        (
            application_id,
            stored_file_id,
            content_sha256,
            document_type,
            status,
            replaced_by_document_id,
            uploaded_by_user_id,
            now,
            now,
        ),
    )
    result = await row.fetchone()
    assert result is not None
    return result["id"]


@pytest.mark.asyncio
async def test_insert_stored_file_and_valid_application_document(
    db_conn: AsyncConnection[Any],
) -> None:
    user = await _insert_test_user(
        db_conn,
        email="docs.schema.ok@example.com",
        full_name="Docs Schema OK",
        role="loan_officer",
        password="test-password-not-a-secret",
    )
    application_id: UUID | None = None
    stored_file_id: UUID | None = None
    try:
        application_id = await _insert_draft_application(
            db_conn,
            created_by_user_id=user["id"],
            reference_code=f"LL-DOC-OK-{uuid4().hex[:6].upper()}",
        )
        stored_file_id = await _insert_stored_file(
            db_conn,
            content_sha256=_HASH_A,
        )
        document_id = await _insert_application_document(
            db_conn,
            application_id=application_id,
            stored_file_id=stored_file_id,
            content_sha256=_HASH_A,
            uploaded_by_user_id=user["id"],
        )
        await db_conn.commit()

        row = await db_conn.execute(
            """
            SELECT d.id,
                   d.content_sha256,
                   d.status::text AS status,
                   d.document_type::text AS document_type,
                   sf.content_sha256 AS file_hash
              FROM application_documents AS d
              JOIN stored_files AS sf ON sf.id = d.stored_file_id
             WHERE d.id = %s
            """,
            (document_id,),
        )
        document = await row.fetchone()
        assert document is not None
        assert document["content_sha256"] == _HASH_A
        assert document["file_hash"] == _HASH_A
        assert document["status"] == "uploaded"
        assert document["document_type"] == "bank_statement"

        trigger = await db_conn.execute(
            """
            SELECT t.tgname
              FROM pg_trigger AS t
              JOIN pg_class AS c ON c.oid = t.tgrelid
             WHERE c.relname = 'application_documents'
               AND t.tgname = 'application_documents_hash_equality_trg'
               AND NOT t.tgisinternal
            """
        )
        assert await trigger.fetchone() is not None

        index = await db_conn.execute(
            """
            SELECT indexname
              FROM pg_indexes
             WHERE schemaname = 'public'
               AND indexname = 'uq_app_docs_active_content'
            """
        )
        assert await index.fetchone() is not None
    finally:
        if application_id is not None:
            await _cleanup_application(db_conn, application_id)
        if stored_file_id is not None:
            await _cleanup_stored_file(db_conn, stored_file_id)
        await _purge_user(db_conn, user["id"])


@pytest.mark.asyncio
async def test_hash_equality_trigger_blocks_mismatched_content_sha256(
    db_conn: AsyncConnection[Any],
) -> None:
    user = await _insert_test_user(
        db_conn,
        email="docs.schema.mismatch@example.com",
        full_name="Docs Schema Mismatch",
        role="loan_officer",
        password="test-password-not-a-secret",
    )
    application_id: UUID | None = None
    stored_file_id: UUID | None = None
    try:
        application_id = await _insert_draft_application(
            db_conn,
            created_by_user_id=user["id"],
            reference_code=f"LL-DOC-MM-{uuid4().hex[:6].upper()}",
        )
        stored_file_id = await _insert_stored_file(
            db_conn,
            content_sha256=_HASH_A,
        )
        await db_conn.commit()

        with pytest.raises(CheckViolation):
            await _insert_application_document(
                db_conn,
                application_id=application_id,
                stored_file_id=stored_file_id,
                content_sha256=_HASH_B,
                uploaded_by_user_id=user["id"],
            )
            await db_conn.commit()
        await db_conn.rollback()

        count = await db_conn.execute(
            """
            SELECT COUNT(*)::int AS n
              FROM application_documents
             WHERE application_id = %s
            """,
            (application_id,),
        )
        row = await count.fetchone()
        assert row is not None
        assert row["n"] == 0
    finally:
        if application_id is not None:
            await _cleanup_application(db_conn, application_id)
        if stored_file_id is not None:
            await _cleanup_stored_file(db_conn, stored_file_id)
        await _purge_user(db_conn, user["id"])


@pytest.mark.asyncio
async def test_partial_unique_index_blocks_duplicate_active_hash(
    db_conn: AsyncConnection[Any],
) -> None:
    user = await _insert_test_user(
        db_conn,
        email="docs.schema.dedup@example.com",
        full_name="Docs Schema Dedup",
        role="loan_officer",
        password="test-password-not-a-secret",
    )
    application_id: UUID | None = None
    stored_file_id: UUID | None = None
    try:
        application_id = await _insert_draft_application(
            db_conn,
            created_by_user_id=user["id"],
            reference_code=f"LL-DOC-DD-{uuid4().hex[:6].upper()}",
        )
        stored_file_id = await _insert_stored_file(
            db_conn,
            content_sha256=_HASH_A,
        )
        await _insert_application_document(
            db_conn,
            application_id=application_id,
            stored_file_id=stored_file_id,
            content_sha256=_HASH_A,
            uploaded_by_user_id=user["id"],
        )
        await db_conn.commit()

        with pytest.raises(UniqueViolation):
            await _insert_application_document(
                db_conn,
                application_id=application_id,
                stored_file_id=stored_file_id,
                content_sha256=_HASH_A,
                uploaded_by_user_id=user["id"],
                document_type="other",
            )
            await db_conn.commit()
        await db_conn.rollback()
    finally:
        if application_id is not None:
            await _cleanup_application(db_conn, application_id)
        if stored_file_id is not None:
            await _cleanup_stored_file(db_conn, stored_file_id)
        await _purge_user(db_conn, user["id"])


@pytest.mark.asyncio
async def test_document_replacement_preserves_history_and_allows_new_hash(
    db_conn: AsyncConnection[Any],
) -> None:
    user = await _insert_test_user(
        db_conn,
        email="docs.schema.replace@example.com",
        full_name="Docs Schema Replace",
        role="loan_officer",
        password="test-password-not-a-secret",
    )
    application_id: UUID | None = None
    stored_file_a: UUID | None = None
    stored_file_b: UUID | None = None
    try:
        application_id = await _insert_draft_application(
            db_conn,
            created_by_user_id=user["id"],
            reference_code=f"LL-DOC-RP-{uuid4().hex[:6].upper()}",
        )
        stored_file_a = await _insert_stored_file(
            db_conn,
            content_sha256=_HASH_A,
            original_filename="old.pdf",
        )
        stored_file_b = await _insert_stored_file(
            db_conn,
            content_sha256=_HASH_B,
            original_filename="new.pdf",
        )
        old_document_id = await _insert_application_document(
            db_conn,
            application_id=application_id,
            stored_file_id=stored_file_a,
            content_sha256=_HASH_A,
            uploaded_by_user_id=user["id"],
        )
        await db_conn.commit()

        new_document_id = await _insert_application_document(
            db_conn,
            application_id=application_id,
            stored_file_id=stored_file_b,
            content_sha256=_HASH_B,
            uploaded_by_user_id=user["id"],
        )
        now = datetime.now(timezone.utc)
        await db_conn.execute(
            """
            UPDATE application_documents
               SET status = 'replaced'::document_status,
                   replaced_by_document_id = %s,
                   updated_at = %s
             WHERE id = %s
            """,
            (new_document_id, now, old_document_id),
        )
        await db_conn.commit()

        rows = await db_conn.execute(
            """
            SELECT id, status::text AS status, content_sha256, replaced_by_document_id
              FROM application_documents
             WHERE application_id = %s
            """,
            (application_id,),
        )
        documents = {row["id"]: row for row in await rows.fetchall()}
        assert len(documents) == 2

        old_row = documents[old_document_id]
        assert old_row["status"] == "replaced"
        assert old_row["content_sha256"] == _HASH_A
        assert old_row["replaced_by_document_id"] == new_document_id

        new_row = documents[new_document_id]
        assert new_row["status"] == "uploaded"
        assert new_row["content_sha256"] == _HASH_B
        assert new_row["replaced_by_document_id"] is None

        # Re-upload of the replaced hash is allowed once the old row is excluded
        # from the partial unique index.
        reupload_id = await _insert_application_document(
            db_conn,
            application_id=application_id,
            stored_file_id=stored_file_a,
            content_sha256=_HASH_A,
            uploaded_by_user_id=user["id"],
            document_type="other",
        )
        await db_conn.commit()
        assert reupload_id != old_document_id
    finally:
        if application_id is not None:
            await _cleanup_application(db_conn, application_id)
        if stored_file_a is not None:
            await _cleanup_stored_file(db_conn, stored_file_a)
        if stored_file_b is not None:
            await _cleanup_stored_file(db_conn, stored_file_b)
        await _purge_user(db_conn, user["id"])


@pytest.mark.asyncio
async def test_stored_files_reject_update_and_delete(
    db_conn: AsyncConnection[Any],
) -> None:
    user = await _insert_test_user(
        db_conn,
        email="docs.schema.immutable@example.com",
        full_name="Docs Schema Immutable",
        role="loan_officer",
        password="test-password-not-a-secret",
    )
    stored_file_id: UUID | None = None
    try:
        stored_file_id = await _insert_stored_file(
            db_conn,
            content_sha256=_HASH_C,
        )
        await db_conn.commit()

        with pytest.raises(InsufficientPrivilege):
            await db_conn.execute(
                """
                UPDATE stored_files
                   SET original_filename = 'mutated.pdf'
                 WHERE id = %s
                """,
                (stored_file_id,),
            )
            await db_conn.commit()
        await db_conn.rollback()

        with pytest.raises(InsufficientPrivilege):
            await db_conn.execute(
                "DELETE FROM stored_files WHERE id = %s",
                (stored_file_id,),
            )
            await db_conn.commit()
        await db_conn.rollback()

        row = await db_conn.execute(
            """
            SELECT original_filename, content_sha256
              FROM stored_files
             WHERE id = %s
            """,
            (stored_file_id,),
        )
        stored = await row.fetchone()
        assert stored is not None
        assert stored["original_filename"] == "statement.pdf"
        assert stored["content_sha256"] == _HASH_C
    finally:
        if stored_file_id is not None:
            await _cleanup_stored_file(db_conn, stored_file_id)
        await _purge_user(db_conn, user["id"])
