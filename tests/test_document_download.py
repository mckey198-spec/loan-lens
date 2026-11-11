"""API tests for document metadata and authorized content streaming (M04-T04)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from psycopg import AsyncConnection

from apps.api.main import app
from apps.api.routers.documents import get_malware_scanner
from modules.intake.scanner import MockMalwareScanner
from modules.intake.signatures import PNG_MAGIC
from shared.settings import get_settings


def _minimal_pdf() -> bytes:
    return b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def _minimal_png() -> bytes:
    return PNG_MAGIC + b"\x00\x00\x00\rIHDR" + b"\x00" * 20


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
            "full_name": "Download Test Applicant",
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
@pytest.mark.parametrize(
    "role_fixture",
    ["test_user", "reviewer_user", "admin_user"],
)
async def test_authorized_roles_can_download_uploaded_content(
    client: AsyncClient,
    test_user: dict[str, Any],
    reviewer_user: dict[str, Any],
    admin_user: dict[str, Any],
    db_conn: AsyncConnection[Any],
    role_fixture: str,
    request: pytest.FixtureRequest,
) -> None:
    actor = request.getfixturevalue(role_fixture)
    officer_csrf = await _login(
        client,
        email=test_user["email"],
        password=test_user["password"],
    )
    application_id = await _create_draft(client, csrf=officer_csrf)
    content = _minimal_pdf()
    digest = hashlib.sha256(content).hexdigest()
    filename = "statement.pdf"

    try:
        upload = await _upload(
            client,
            application_id=application_id,
            csrf=officer_csrf,
            filename=filename,
            content=content,
            content_type="application/pdf",
        )
        assert upload.status_code == 201, upload.text
        document_id = UUID(upload.json()["id"])
        assert "storage_uri" not in upload.json()

        await client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": officer_csrf})
        await _login(
            client,
            email=actor["email"],
            password=actor["password"],
        )

        response = await client.get(
            f"/api/v1/applications/{application_id}/documents/{document_id}/content"
        )
        assert response.status_code == 200, response.text
        assert response.content == content
        assert response.headers["content-type"].startswith("application/pdf")
        disposition = response.headers.get("content-disposition", "")
        assert "inline" in disposition.lower()
        assert filename in disposition

        audit = await db_conn.execute(
            """
            SELECT action, entity_type, entity_id
            FROM audit_events
            WHERE application_id = %s
              AND action = 'document.download'
              AND entity_id = %s
            ORDER BY chain_seq DESC
            LIMIT 1
            """,
            (application_id, document_id),
        )
        row = await audit.fetchone()
        assert row is not None
        assert row["action"] == "document.download"
        assert row["entity_type"] == "application_documents"
        assert row["entity_id"] == document_id
    finally:
        await _cleanup_upload_artifacts(
            db_conn,
            application_id=application_id,
            content_hashes=[digest],
        )


@pytest.mark.asyncio
async def test_metadata_responses_omit_storage_uri(
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
    content = _minimal_png()
    digest = hashlib.sha256(content).hexdigest()

    try:
        upload = await _upload(
            client,
            application_id=application_id,
            csrf=csrf,
            filename="scan.png",
            content=content,
            content_type="image/png",
        )
        assert upload.status_code == 201, upload.text
        body = upload.json()
        document_id = UUID(body["id"])
        assert "storage_uri" not in body

        listed = await client.get(f"/api/v1/applications/{application_id}/documents")
        assert listed.status_code == 200, listed.text
        items = listed.json()
        assert isinstance(items, list)
        assert len(items) == 1
        assert "storage_uri" not in items[0]
        assert items[0]["id"] == str(document_id)

        meta = await client.get(
            f"/api/v1/applications/{application_id}/documents/{document_id}"
        )
        assert meta.status_code == 200, meta.text
        meta_body = meta.json()
        assert "storage_uri" not in meta_body
        assert meta_body["original_filename"] == "scan.png"
        assert meta_body["mime_type"] == "image/png"
        assert meta_body["content_sha256"] == digest
    finally:
        await _cleanup_upload_artifacts(
            db_conn,
            application_id=application_id,
            content_hashes=[digest],
        )


@pytest.mark.asyncio
async def test_download_wrong_application_returns_404(
    client: AsyncClient,
    test_user: dict[str, Any],
    db_conn: AsyncConnection[Any],
) -> None:
    csrf = await _login(
        client,
        email=test_user["email"],
        password=test_user["password"],
    )
    application_a = await _create_draft(client, csrf=csrf)
    application_b = await _create_draft(client, csrf=csrf)
    content = _minimal_pdf()
    digest = hashlib.sha256(content).hexdigest()
    hashes_a = [digest]
    hashes_b: list[str] = []

    try:
        upload = await _upload(
            client,
            application_id=application_a,
            csrf=csrf,
            filename="statement.pdf",
            content=content,
            content_type="application/pdf",
        )
        assert upload.status_code == 201, upload.text
        document_id = UUID(upload.json()["id"])

        response = await client.get(
            f"/api/v1/applications/{application_b}/documents/{document_id}/content"
        )
        assert response.status_code == 404, response.text
        assert response.json()["code"] == "DOCUMENT_NOT_FOUND"

        meta = await client.get(
            f"/api/v1/applications/{application_b}/documents/{document_id}"
        )
        assert meta.status_code == 404, meta.text

        audit = await db_conn.execute(
            """
            SELECT COUNT(*)::int AS n
            FROM audit_events
            WHERE action = 'document.download'
              AND entity_id = %s
            """,
            (document_id,),
        )
        row = await audit.fetchone()
        assert row is not None
        assert row["n"] == 0
    finally:
        await _cleanup_upload_artifacts(
            db_conn,
            application_id=application_a,
            content_hashes=hashes_a,
        )
        await _cleanup_upload_artifacts(
            db_conn,
            application_id=application_b,
            content_hashes=hashes_b,
        )


@pytest.mark.asyncio
async def test_unauthenticated_download_returns_401(
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
        upload = await _upload(
            client,
            application_id=application_id,
            csrf=csrf,
            filename="statement.pdf",
            content=content,
            content_type="application/pdf",
        )
        assert upload.status_code == 201, upload.text
        document_id = UUID(upload.json()["id"])

        await client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})

        response = await client.get(
            f"/api/v1/applications/{application_id}/documents/{document_id}/content"
        )
        assert response.status_code == 401, response.text

        listed = await client.get(f"/api/v1/applications/{application_id}/documents")
        assert listed.status_code == 401, listed.text

        meta = await client.get(
            f"/api/v1/applications/{application_id}/documents/{document_id}"
        )
        assert meta.status_code == 401, meta.text

        # Unknown document id also requires auth first.
        missing = await client.get(
            f"/api/v1/applications/{application_id}/documents/{uuid4()}/content"
        )
        assert missing.status_code == 401, missing.text
    finally:
        await _cleanup_upload_artifacts(
            db_conn,
            application_id=application_id,
            content_hashes=[digest],
        )
