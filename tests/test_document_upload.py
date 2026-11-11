"""API tests for document upload, scan, and persistence (M04-T02 / M04-T03)."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from psycopg import AsyncConnection

from apps.api.main import app
from apps.api.routers.documents import get_malware_scanner
from modules.intake.scanner import EICAR_TEST_BYTES, MockMalwareScanner
from modules.intake.signatures import JPEG_MAGIC, PDF_MAGIC, PNG_MAGIC
from shared.settings import get_settings

_STATUS_PATHS: dict[str, list[tuple[str, str]]] = {
    "draft": [],
    "in_review": [
        ("draft", "submitted"),
        ("submitted", "processing"),
        ("processing", "awaiting_human_review"),
        ("awaiting_human_review", "in_review"),
    ],
    "review_completed": [
        ("draft", "submitted"),
        ("submitted", "processing"),
        ("processing", "awaiting_human_review"),
        ("awaiting_human_review", "in_review"),
        ("in_review", "review_completed"),
    ],
}


def _minimal_pdf() -> bytes:
    return b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def _minimal_png() -> bytes:
    return PNG_MAGIC + b"\x00\x00\x00\rIHDR" + b"\x00" * 20


def _minimal_jpeg() -> bytes:
    return JPEG_MAGIC + b"\xe0\x00\x10JFIF" + b"\x00" * 32


def _infected_pdf() -> bytes:
    return b"%PDF-1.4\n" + EICAR_TEST_BYTES + b"\n%%EOF\n"


async def _login(
    client: AsyncClient,
    *,
    email: str,
    password: str,
) -> str:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


async def _cleanup_upload_artifacts(
    db_conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    content_hashes: list[str],
) -> None:
    await db_conn.rollback()
    await db_conn.execute(
        "SELECT set_config('loanlens.allow_audit_append', 'on', true)"
    )
    await db_conn.execute(
        "DELETE FROM audit_events WHERE application_id = %s",
        (application_id,),
    )
    await db_conn.execute(
        """
        UPDATE audit_chain_state AS s
        SET last_chain_seq = COALESCE(e.max_seq, 0),
            last_event_hash = COALESCE(
                e.last_hash,
                '0000000000000000000000000000000000000000000000000000000000000000'
            )
        FROM (
            SELECT MAX(chain_seq) AS max_seq,
                   (ARRAY_AGG(event_hash ORDER BY chain_seq DESC))[1] AS last_hash
            FROM audit_events
        ) AS e
        WHERE s.id = 1
        """
    )
    await db_conn.execute(
        "DELETE FROM application_documents WHERE application_id = %s",
        (application_id,),
    )
    for digest in content_hashes:
        still_used = await db_conn.execute(
            """
            SELECT 1 AS present
            FROM application_documents
            WHERE content_sha256 = %s
            LIMIT 1
            """,
            (digest,),
        )
        if await still_used.fetchone() is None:
            await db_conn.execute(
                "ALTER TABLE stored_files DISABLE TRIGGER stored_files_immutable_trg"
            )
            await db_conn.execute(
                "DELETE FROM stored_files WHERE content_sha256 = %s",
                (digest,),
            )
            await db_conn.execute(
                "ALTER TABLE stored_files ENABLE TRIGGER stored_files_immutable_trg"
            )
            clean = Path(get_settings().CLEAN_UPLOADS_DIR) / digest
            if clean.is_file():
                clean.unlink()
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


def _valid_create_payload() -> dict[str, Any]:
    return {
        "applicant_type": "salaried",
        "requested_amount": "500000.00",
        "tenure_months": 36,
        "loan_purpose": "home_renovation",
        "applicant": {
            "full_name": "Upload Test Applicant",
            "date_of_birth": "1990-01-15",
            "pan": "ABCDE1234F",
            "current_address": "12 Example Road, Bhubaneswar",
            "employer_or_business_name": "Example Employer Pvt Ltd",
            "time_in_job_or_business_months": 24,
            "gross_monthly_income": "100000.00",
            "net_monthly_income": "80000.00",
            "total_existing_monthly_emis": "5000.00",
        },
    }


async def _create_draft(client: AsyncClient, *, csrf: str) -> UUID:
    response = await client.post(
        "/api/v1/applications",
        headers={"X-CSRF-Token": csrf},
        json=_valid_create_payload(),
    )
    assert response.status_code == 201, response.text
    return UUID(response.json()["id"])


async def _force_status(
    db_conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    actor_user_id: UUID,
    target_status: str,
) -> None:
    path = _STATUS_PATHS[target_status]
    now = datetime.now(timezone.utc)
    for from_status, to_status in path:
        await db_conn.execute(
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
            """,
            (
                application_id,
                from_status,
                to_status,
                actor_user_id,
                f"test force {from_status}->{to_status}",
                now,
            ),
        )
        await db_conn.execute(
            """
            UPDATE applications
               SET status = %s::application_status,
                   updated_at = %s
             WHERE id = %s
            """,
            (to_status, now, application_id),
        )
    await db_conn.commit()


async def _upload(
    client: AsyncClient,
    *,
    application_id: UUID,
    csrf: str,
    filename: str,
    content: bytes,
    content_type: str,
    document_type: str = "bank_statement",
) -> Any:
    return await client.post(
        f"/api/v1/applications/{application_id}/documents",
        headers={"X-CSRF-Token": csrf},
        files={"file": (filename, content, content_type)},
        data={"document_type": document_type},
    )


@pytest.fixture
def mock_scanner() -> MockMalwareScanner:
    return MockMalwareScanner()


@pytest.fixture(autouse=True)
def _override_scanner(mock_scanner: MockMalwareScanner):
    app.dependency_overrides[get_malware_scanner] = lambda: mock_scanner
    get_settings.cache_clear()
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_malware_scanner, None)
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_loan_officer_uploads_pdf_png_jpg_in_draft(
    client: AsyncClient,
    test_user: dict[str, Any],
    db_conn: AsyncConnection[Any],
) -> None:
    csrf = await _login(
        client,
        email=test_user["email"],
        password=test_user["password"],
    )
    application_id = await _create_draft(client, csrf=csrf)
    hashes: list[str] = []
    try:
        cases = [
            ("statement.pdf", _minimal_pdf(), "application/pdf"),
            ("scan.png", _minimal_png(), "image/png"),
            ("photo.jpg", _minimal_jpeg(), "image/jpeg"),
        ]
        for filename, content, mime in cases:
            response = await _upload(
                client,
                application_id=application_id,
                csrf=csrf,
                filename=filename,
                content=content,
                content_type=mime,
            )
            assert response.status_code == 201, response.text
            body = response.json()
            digest = hashlib.sha256(content).hexdigest()
            hashes.append(digest)
            assert body["content_sha256"] == digest
            assert body["status"] == "uploaded"
            assert body["original_filename"] == filename
            assert body["mime_type"] == mime
            assert "storage_uri" not in body

            audit = await db_conn.execute(
                """
                SELECT action
                FROM audit_events
                WHERE application_id = %s AND action = 'document.upload'
                ORDER BY chain_seq DESC
                LIMIT 1
                """,
                (application_id,),
            )
            row = await audit.fetchone()
            assert row is not None
            assert row["action"] == "document.upload"
    finally:
        await _cleanup_upload_artifacts(
            db_conn,
            application_id=application_id,
            content_hashes=hashes,
        )


@pytest.mark.asyncio
async def test_upload_rejected_when_magic_bytes_mismatch_extension(
    client: AsyncClient,
    test_user: dict[str, Any],
    db_conn: AsyncConnection[Any],
) -> None:
    csrf = await _login(
        client,
        email=test_user["email"],
        password=test_user["password"],
    )
    application_id = await _create_draft(client, csrf=csrf)
    try:
        # Text / PE-like bytes renamed to .pdf
        fake = b"MZ\x90\x00This is not a PDF"
        response = await _upload(
            client,
            application_id=application_id,
            csrf=csrf,
            filename="malware.pdf",
            content=fake,
            content_type="application/pdf",
        )
        assert response.status_code == 422, response.text
        body = response.json()
        assert body["code"] == "DOCUMENT_SIGNATURE_INVALID"

        count = await db_conn.execute(
            "SELECT COUNT(*)::int AS n FROM application_documents WHERE application_id = %s",
            (application_id,),
        )
        row = await count.fetchone()
        assert row is not None
        assert row["n"] == 0
    finally:
        await _cleanup_upload_artifacts(
            db_conn,
            application_id=application_id,
            content_hashes=[],
        )


@pytest.mark.asyncio
async def test_upload_rejected_when_file_exceeds_25mb(
    client: AsyncClient,
    test_user: dict[str, Any],
    db_conn: AsyncConnection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Keep the fixture fast: temporarily lower the configured max size.
    monkeypatch.setenv("MAX_UPLOAD_SIZE_BYTES", "1024")
    get_settings.cache_clear()
    csrf = await _login(
        client,
        email=test_user["email"],
        password=test_user["password"],
    )
    application_id = await _create_draft(client, csrf=csrf)
    try:
        oversized = PDF_MAGIC + (b"A" * 2048)
        response = await _upload(
            client,
            application_id=application_id,
            csrf=csrf,
            filename="big.pdf",
            content=oversized,
            content_type="application/pdf",
        )
        assert response.status_code == 413, response.text
        assert response.json()["code"] == "DOCUMENT_TOO_LARGE"
    finally:
        monkeypatch.delenv("MAX_UPLOAD_SIZE_BYTES", raising=False)
        get_settings.cache_clear()
        await _cleanup_upload_artifacts(
            db_conn,
            application_id=application_id,
            content_hashes=[],
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("target_status", ["in_review", "review_completed"])
async def test_upload_rejected_in_disallowed_application_status(
    client: AsyncClient,
    test_user: dict[str, Any],
    db_conn: AsyncConnection[Any],
    target_status: str,
) -> None:
    csrf = await _login(
        client,
        email=test_user["email"],
        password=test_user["password"],
    )
    application_id = await _create_draft(client, csrf=csrf)
    await _force_status(
        db_conn,
        application_id=application_id,
        actor_user_id=test_user["id"],
        target_status=target_status,
    )
    try:
        response = await _upload(
            client,
            application_id=application_id,
            csrf=csrf,
            filename="statement.pdf",
            content=_minimal_pdf(),
            content_type="application/pdf",
        )
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "APPLICATION_INVALID_STATE"
    finally:
        await _cleanup_upload_artifacts(
            db_conn,
            application_id=application_id,
            content_hashes=[],
        )


@pytest.mark.asyncio
async def test_reviewer_cannot_upload_documents(
    client: AsyncClient,
    test_user: dict[str, Any],
    reviewer_user: dict[str, Any],
    db_conn: AsyncConnection[Any],
) -> None:
    officer_csrf = await _login(
        client,
        email=test_user["email"],
        password=test_user["password"],
    )
    application_id = await _create_draft(client, csrf=officer_csrf)
    await client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": officer_csrf})

    reviewer_csrf = await _login(
        client,
        email=reviewer_user["email"],
        password=reviewer_user["password"],
    )
    try:
        response = await _upload(
            client,
            application_id=application_id,
            csrf=reviewer_csrf,
            filename="statement.pdf",
            content=_minimal_pdf(),
            content_type="application/pdf",
        )
        assert response.status_code == 403, response.text
        assert response.json()["code"] == "AUTH_FORBIDDEN"
    finally:
        await _cleanup_upload_artifacts(
            db_conn,
            application_id=application_id,
            content_hashes=[],
        )


@pytest.mark.asyncio
async def test_duplicate_active_upload_returns_conflict(
    client: AsyncClient,
    test_user: dict[str, Any],
    db_conn: AsyncConnection[Any],
) -> None:
    csrf = await _login(
        client,
        email=test_user["email"],
        password=test_user["password"],
    )
    application_id = await _create_draft(client, csrf=csrf)
    content = _minimal_pdf()
    digest = hashlib.sha256(content).hexdigest()
    try:
        first = await _upload(
            client,
            application_id=application_id,
            csrf=csrf,
            filename="statement.pdf",
            content=content,
            content_type="application/pdf",
        )
        assert first.status_code == 201, first.text

        second = await _upload(
            client,
            application_id=application_id,
            csrf=csrf,
            filename="statement-copy.pdf",
            content=content,
            content_type="application/pdf",
        )
        assert second.status_code == 409, second.text
        body = second.json()
        assert body["code"] == "DOCUMENT_DUPLICATE"

        count = await db_conn.execute(
            """
            SELECT COUNT(*)::int AS n
            FROM application_documents
            WHERE application_id = %s AND content_sha256 = %s
            """,
            (application_id, digest),
        )
        row = await count.fetchone()
        assert row is not None
        assert row["n"] == 1
    finally:
        await _cleanup_upload_artifacts(
            db_conn,
            application_id=application_id,
            content_hashes=[digest],
        )


@pytest.mark.asyncio
async def test_infected_file_rejected_and_no_document_row(
    client: AsyncClient,
    test_user: dict[str, Any],
    db_conn: AsyncConnection[Any],
) -> None:
    csrf = await _login(
        client,
        email=test_user["email"],
        password=test_user["password"],
    )
    application_id = await _create_draft(client, csrf=csrf)
    content = _infected_pdf()
    digest = hashlib.sha256(content).hexdigest()
    try:
        response = await _upload(
            client,
            application_id=application_id,
            csrf=csrf,
            filename="eicar.pdf",
            content=content,
            content_type="application/pdf",
        )
        assert response.status_code == 422, response.text
        assert response.json()["code"] == "DOCUMENT_INFECTED"

        docs = await db_conn.execute(
            "SELECT COUNT(*)::int AS n FROM application_documents WHERE application_id = %s",
            (application_id,),
        )
        doc_row = await docs.fetchone()
        assert doc_row is not None
        assert doc_row["n"] == 0

        stored = await db_conn.execute(
            "SELECT COUNT(*)::int AS n FROM stored_files WHERE content_sha256 = %s",
            (digest,),
        )
        stored_row = await stored.fetchone()
        assert stored_row is not None
        assert stored_row["n"] == 0

        clean = Path(get_settings().CLEAN_UPLOADS_DIR) / digest
        assert not clean.exists()
    finally:
        await _cleanup_upload_artifacts(
            db_conn,
            application_id=application_id,
            content_hashes=[digest],
        )
