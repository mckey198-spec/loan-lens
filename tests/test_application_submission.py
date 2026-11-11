"""API and transaction tests for application submit (M06-T03)."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from psycopg import AsyncConnection

from apps.api.main import app
from apps.api.routers.documents import get_malware_scanner
from modules.intake.scanner import MockMalwareScanner
from modules.intake.service import _canonical_snapshot_hash, _decimal_str
from modules.policy.service import (
    activate_policy_version,
    create_policy_document,
    create_policy_version,
)
from modules.worker.service import claim_and_prepare
from shared.settings import get_settings
from tests.conftest import _insert_test_user, _purge_user
from tests.test_policies_and_snapshots_schema import (
    _cleanup_application,
    _cleanup_policy_document,
)


def _minimal_pdf() -> bytes:
    return b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


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


def _valid_create_payload() -> dict[str, Any]:
    return {
        "applicant_type": "salaried",
        "requested_amount": "500000.00",
        "tenure_months": 36,
        "loan_purpose": "home_renovation",
        "applicant": {
            "full_name": "Submit Test Applicant",
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


def _threshold_payload() -> dict[str, Any]:
    return {
        "applicant_type": "salaried",
        "min_monthly_income": "25000.00",
        "max_foir_pct": "50.00",
        "min_employment_months": 12,
        "max_loan_amount": "2500000.00",
    }


async def _create_draft(client: AsyncClient, *, csrf: str) -> UUID:
    response = await client.post(
        "/api/v1/applications",
        headers={"X-CSRF-Token": csrf},
        json=_valid_create_payload(),
    )
    assert response.status_code == 201, response.text
    return UUID(response.json()["id"])


async def _upload_pdf(
    client: AsyncClient,
    *,
    application_id: UUID,
    csrf: str,
) -> dict[str, Any]:
    content = _minimal_pdf()
    response = await client.post(
        f"/api/v1/applications/{application_id}/documents",
        headers={"X-CSRF-Token": csrf},
        files={"file": ("statement.pdf", content, "application/pdf")},
        data={"document_type": "bank_statement"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    body["_content_sha256_expected"] = hashlib.sha256(content).hexdigest()
    return body


async def _seed_active_policy(
    *,
    pool: Any,
    admin_user_id: UUID,
    annual_interest_rate_pct: Decimal = Decimal("10.50"),
    max_foir_pct: Decimal = Decimal("50.00"),
    effective_from: date | None = None,
    effective_to: date | None = None,
) -> tuple[UUID, UUID]:
    today = datetime.now(timezone.utc).date()
    document = await create_policy_document(
        pool,
        title="Submit Test Policy",
        lender_label="synthetic_demo",
        created_by_user_id=admin_user_id,
    )
    thresholds = [_threshold_payload()]
    thresholds[0]["max_foir_pct"] = str(max_foir_pct)
    version = await create_policy_version(
        pool,
        policy_document_id=document.id,
        version_label="submit-v1",
        annual_interest_rate_pct=annual_interest_rate_pct,
        effective_from=effective_from or (today - timedelta(days=1)),
        effective_to=effective_to or (today + timedelta(days=365)),
        thresholds=thresholds,
        doc_requirements=[],
        created_by_user_id=admin_user_id,
    )
    activated = await activate_policy_version(
        pool,
        policy_version_id=version.id,
        actor_user_id=admin_user_id,
        request_id="test-submit-activate",
    )
    assert activated.status == "active"
    return document.id, version.id


async def _cleanup_submit_artifacts(
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
    await _cleanup_application(db_conn, application_id)
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
    await db_conn.commit()


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
async def test_loan_officer_submits_draft_with_document_and_policy(
    client: AsyncClient,
    db_conn: AsyncConnection[Any],
) -> None:
    officer = await _insert_test_user(
        db_conn,
        email="submit.officer@example.com",
        full_name="Submit Officer",
        role="loan_officer",
        password="officer-password-not-a-secret",
    )
    admin = await _insert_test_user(
        db_conn,
        email="submit.admin@example.com",
        full_name="Submit Admin",
        role="admin",
        password="admin-password-not-a-secret",
    )
    pool = app.state.pool
    assert pool is not None

    policy_document_id: UUID | None = None
    application_id: UUID | None = None
    content_hashes: list[str] = []
    csrf = ""

    try:
        policy_document_id, policy_version_id = await _seed_active_policy(
            pool=pool,
            admin_user_id=admin["id"],
            annual_interest_rate_pct=Decimal("10.50"),
            max_foir_pct=Decimal("50.00"),
        )

        csrf = await _login(
            client,
            email=officer["email"],
            password=officer["password"],
        )
        application_id = await _create_draft(client, csrf=csrf)
        document = await _upload_pdf(
            client,
            application_id=application_id,
            csrf=csrf,
        )
        content_hashes.append(document["_content_sha256_expected"])

        submit = await client.post(
            f"/api/v1/applications/{application_id}/submit",
            headers={"X-CSRF-Token": csrf},
        )
        assert submit.status_code == 200, submit.text
        body = submit.json()
        assert body["status"] == "submitted"
        assert body["submitted_at"] is not None
        assert body["applicable_policy_version_id"] == str(policy_version_id)

        snapshot_row = await db_conn.execute(
            """
            SELECT
                id,
                snapshot_number,
                snapshot_hash,
                as_of_date,
                document_inventory_json,
                applicable_policy_version_id,
                declared_gross_monthly_income,
                declared_net_monthly_income,
                declared_existing_emis,
                requested_amount,
                tenure_months,
                loan_purpose,
                applicant_type::text AS applicant_type,
                declared_other_income,
                declared_household_expenses
              FROM application_input_snapshots
             WHERE application_id = %s
            """,
            (application_id,),
        )
        snapshot = await snapshot_row.fetchone()
        assert snapshot is not None
        assert snapshot["snapshot_number"] == 1
        assert snapshot["applicable_policy_version_id"] == policy_version_id

        inventory = snapshot["document_inventory_json"]
        assert isinstance(inventory, list)
        assert len(inventory) == 1
        assert inventory[0]["document_id"] == document["id"]
        assert inventory[0]["document_type"] == "bank_statement"
        assert inventory[0]["content_sha256"] == document["content_sha256"]

        expected_hash = _canonical_snapshot_hash(
            {
                "application_id": str(application_id),
                "applicable_policy_version_id": str(policy_version_id),
                "applicant_type": snapshot["applicant_type"],
                "as_of_date": snapshot["as_of_date"].isoformat(),
                "declared_existing_emis": _decimal_str(
                    snapshot["declared_existing_emis"]
                ),
                "declared_gross_monthly_income": _decimal_str(
                    snapshot["declared_gross_monthly_income"]
                ),
                "declared_household_expenses": _decimal_str(
                    snapshot["declared_household_expenses"]
                ),
                "declared_net_monthly_income": _decimal_str(
                    snapshot["declared_net_monthly_income"]
                ),
                "declared_other_income": _decimal_str(
                    snapshot["declared_other_income"]
                ),
                "document_inventory": inventory,
                "loan_purpose": snapshot["loan_purpose"],
                "requested_amount": _decimal_str(snapshot["requested_amount"]),
                "snapshot_number": snapshot["snapshot_number"],
                "tenure_months": snapshot["tenure_months"],
            }
        )
        assert snapshot["snapshot_hash"] == expected_hash
        assert len(snapshot["snapshot_hash"]) == 64

        calc_row = await db_conn.execute(
            """
            SELECT
                monthly_gross_income,
                monthly_net_income,
                monthly_obligations,
                requested_loan_amount,
                tenure_months,
                annual_interest_rate_pct,
                max_allowed_foir_pct,
                input_snapshot_id
              FROM snapshot_calculation_inputs
             WHERE application_id = %s
            """,
            (application_id,),
        )
        calc = await calc_row.fetchone()
        assert calc is not None
        assert calc["input_snapshot_id"] == snapshot["id"]
        assert calc["monthly_gross_income"] == Decimal("100000.00")
        assert calc["monthly_net_income"] == Decimal("80000.00")
        assert calc["monthly_obligations"] == Decimal("5000.00")
        assert calc["requested_loan_amount"] == Decimal("500000.00")
        assert calc["tenure_months"] == 36
        assert calc["annual_interest_rate_pct"] == Decimal("10.50")
        assert calc["max_allowed_foir_pct"] == Decimal("50.00")

        run_row = await db_conn.execute(
            """
            SELECT
                id,
                status::text AS status,
                trigger,
                input_snapshot_id
              FROM processing_runs
             WHERE application_id = %s
            """,
            (application_id,),
        )
        run = await run_row.fetchone()
        assert run is not None
        assert run["status"] == "queued"
        assert run["trigger"] == "application_submission"
        assert run["input_snapshot_id"] == snapshot["id"]

        history = await db_conn.execute(
            """
            SELECT from_status::text AS from_status,
                   to_status::text AS to_status,
                   actor_type::text AS actor_type
              FROM application_state_history
             WHERE application_id = %s
               AND to_status = 'submitted'::application_status
            """,
            (application_id,),
        )
        hist = await history.fetchone()
        assert hist is not None
        assert hist["from_status"] == "draft"
        assert hist["to_status"] == "submitted"
        assert hist["actor_type"] == "user"

        # Snapshot immutability: UPDATE must be rejected by append-only trigger.
        with pytest.raises(Exception):
            await db_conn.execute(
                """
                UPDATE application_input_snapshots
                   SET snapshot_hash = %s
                 WHERE id = %s
                """,
                ("b" * 64, snapshot["id"]),
            )
            await db_conn.commit()
        await db_conn.rollback()

        # Prefer this run under SKIP LOCKED ordering used by claim_next_run.
        await db_conn.execute(
            """
            UPDATE processing_runs
               SET created_at = %s
             WHERE id = %s
            """,
            (datetime(2000, 1, 1, tzinfo=timezone.utc), run["id"]),
        )
        await db_conn.commit()

        # Worker claim moves submitted → processing and run → running.
        async with pool.connection() as conn:
            async with conn.transaction():
                claim = await claim_and_prepare(
                    conn,
                    worker_id="submit-test-worker",
                    lease_seconds=60,
                )
        assert claim is not None
        assert claim.id == run["id"]
        assert claim.status == "running"

        app_status = await db_conn.execute(
            "SELECT status::text AS status FROM applications WHERE id = %s",
            (application_id,),
        )
        status_row = await app_status.fetchone()
        assert status_row is not None
        assert status_row["status"] == "processing"
    finally:
        if application_id is not None:
            await _cleanup_submit_artifacts(
                db_conn,
                application_id=application_id,
                content_hashes=content_hashes,
            )
        if policy_document_id is not None:
            await _cleanup_policy_document(db_conn, policy_document_id)
        if csrf:
            await client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
        await _purge_user(db_conn, officer["id"])
        await _purge_user(db_conn, admin["id"])


@pytest.mark.asyncio
async def test_submit_fails_without_active_policy(
    client: AsyncClient,
    db_conn: AsyncConnection[Any],
) -> None:
    officer = await _insert_test_user(
        db_conn,
        email="submit.nopolicy@example.com",
        full_name="Submit No Policy",
        role="loan_officer",
        password="officer-password-not-a-secret",
    )
    admin = await _insert_test_user(
        db_conn,
        email="submit.nopolicy.admin@example.com",
        full_name="Submit No Policy Admin",
        role="admin",
        password="admin-password-not-a-secret",
    )
    pool = app.state.pool
    assert pool is not None

    policy_document_id: UUID | None = None
    application_id: UUID | None = None
    content_hashes: list[str] = []
    csrf = ""

    try:
        # Active range that does not cover today.
        today = datetime.now(timezone.utc).date()
        policy_document_id, _ = await _seed_active_policy(
            pool=pool,
            admin_user_id=admin["id"],
            effective_from=today - timedelta(days=60),
            effective_to=today - timedelta(days=30),
        )

        csrf = await _login(
            client,
            email=officer["email"],
            password=officer["password"],
        )
        application_id = await _create_draft(client, csrf=csrf)
        document = await _upload_pdf(
            client,
            application_id=application_id,
            csrf=csrf,
        )
        content_hashes.append(document["_content_sha256_expected"])

        submit = await client.post(
            f"/api/v1/applications/{application_id}/submit",
            headers={"X-CSRF-Token": csrf},
        )
        assert submit.status_code == 422, submit.text
        assert submit.json()["code"] == "NO_ACTIVE_POLICY_FOUND"

        status = await db_conn.execute(
            "SELECT status::text AS status FROM applications WHERE id = %s",
            (application_id,),
        )
        row = await status.fetchone()
        assert row is not None
        assert row["status"] == "draft"

        snaps = await db_conn.execute(
            "SELECT COUNT(*)::int AS total FROM application_input_snapshots "
            "WHERE application_id = %s",
            (application_id,),
        )
        count = await snaps.fetchone()
        assert count is not None
        assert count["total"] == 0
    finally:
        if application_id is not None:
            await _cleanup_submit_artifacts(
                db_conn,
                application_id=application_id,
                content_hashes=content_hashes,
            )
        if policy_document_id is not None:
            await _cleanup_policy_document(db_conn, policy_document_id)
        if csrf:
            await client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
        await _purge_user(db_conn, officer["id"])
        await _purge_user(db_conn, admin["id"])


@pytest.mark.asyncio
async def test_submit_fails_without_documents(
    client: AsyncClient,
    db_conn: AsyncConnection[Any],
) -> None:
    officer = await _insert_test_user(
        db_conn,
        email="submit.nodocs@example.com",
        full_name="Submit No Docs",
        role="loan_officer",
        password="officer-password-not-a-secret",
    )
    admin = await _insert_test_user(
        db_conn,
        email="submit.nodocs.admin@example.com",
        full_name="Submit No Docs Admin",
        role="admin",
        password="admin-password-not-a-secret",
    )
    pool = app.state.pool
    assert pool is not None

    policy_document_id: UUID | None = None
    application_id: UUID | None = None
    csrf = ""

    try:
        policy_document_id, _ = await _seed_active_policy(
            pool=pool,
            admin_user_id=admin["id"],
        )
        csrf = await _login(
            client,
            email=officer["email"],
            password=officer["password"],
        )
        application_id = await _create_draft(client, csrf=csrf)

        submit = await client.post(
            f"/api/v1/applications/{application_id}/submit",
            headers={"X-CSRF-Token": csrf},
        )
        assert submit.status_code == 422, submit.text
        assert submit.json()["code"] == "NO_DOCUMENTS_UPLOADED"
    finally:
        if application_id is not None:
            await _cleanup_submit_artifacts(
                db_conn,
                application_id=application_id,
                content_hashes=[],
            )
        if policy_document_id is not None:
            await _cleanup_policy_document(db_conn, policy_document_id)
        if csrf:
            await client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
        await _purge_user(db_conn, officer["id"])
        await _purge_user(db_conn, admin["id"])


@pytest.mark.asyncio
async def test_reviewer_forbidden_on_submit(
    client: AsyncClient,
    db_conn: AsyncConnection[Any],
) -> None:
    officer = await _insert_test_user(
        db_conn,
        email="submit.rev.officer@example.com",
        full_name="Submit Rev Officer",
        role="loan_officer",
        password="officer-password-not-a-secret",
    )
    reviewer = await _insert_test_user(
        db_conn,
        email="submit.rev.reviewer@example.com",
        full_name="Submit Reviewer",
        role="reviewer",
        password="reviewer-password-not-a-secret",
    )
    admin = await _insert_test_user(
        db_conn,
        email="submit.rev.admin@example.com",
        full_name="Submit Rev Admin",
        role="admin",
        password="admin-password-not-a-secret",
    )
    pool = app.state.pool
    assert pool is not None

    policy_document_id: UUID | None = None
    application_id: UUID | None = None
    content_hashes: list[str] = []
    officer_csrf = ""
    reviewer_csrf = ""

    try:
        policy_document_id, _ = await _seed_active_policy(
            pool=pool,
            admin_user_id=admin["id"],
        )
        officer_csrf = await _login(
            client,
            email=officer["email"],
            password=officer["password"],
        )
        application_id = await _create_draft(client, csrf=officer_csrf)
        document = await _upload_pdf(
            client,
            application_id=application_id,
            csrf=officer_csrf,
        )
        content_hashes.append(document["_content_sha256_expected"])
        await client.post(
            "/api/v1/auth/logout",
            headers={"X-CSRF-Token": officer_csrf},
        )
        officer_csrf = ""

        reviewer_csrf = await _login(
            client,
            email=reviewer["email"],
            password=reviewer["password"],
        )
        submit = await client.post(
            f"/api/v1/applications/{application_id}/submit",
            headers={"X-CSRF-Token": reviewer_csrf},
        )
        assert submit.status_code == 403, submit.text
        assert submit.json()["code"] == "AUTH_FORBIDDEN"
    finally:
        if application_id is not None:
            await _cleanup_submit_artifacts(
                db_conn,
                application_id=application_id,
                content_hashes=content_hashes,
            )
        if policy_document_id is not None:
            await _cleanup_policy_document(db_conn, policy_document_id)
        if officer_csrf:
            await client.post(
                "/api/v1/auth/logout",
                headers={"X-CSRF-Token": officer_csrf},
            )
        if reviewer_csrf:
            await client.post(
                "/api/v1/auth/logout",
                headers={"X-CSRF-Token": reviewer_csrf},
            )
        await _purge_user(db_conn, officer["id"])
        await _purge_user(db_conn, reviewer["id"])
        await _purge_user(db_conn, admin["id"])


@pytest.mark.asyncio
async def test_submit_non_draft_returns_conflict(
    client: AsyncClient,
    db_conn: AsyncConnection[Any],
) -> None:
    officer = await _insert_test_user(
        db_conn,
        email="submit.conflict@example.com",
        full_name="Submit Conflict Officer",
        role="loan_officer",
        password="officer-password-not-a-secret",
    )
    admin = await _insert_test_user(
        db_conn,
        email="submit.conflict.admin@example.com",
        full_name="Submit Conflict Admin",
        role="admin",
        password="admin-password-not-a-secret",
    )
    pool = app.state.pool
    assert pool is not None

    policy_document_id: UUID | None = None
    application_id: UUID | None = None
    content_hashes: list[str] = []
    csrf = ""

    try:
        policy_document_id, _ = await _seed_active_policy(
            pool=pool,
            admin_user_id=admin["id"],
        )
        csrf = await _login(
            client,
            email=officer["email"],
            password=officer["password"],
        )
        application_id = await _create_draft(client, csrf=csrf)
        document = await _upload_pdf(
            client,
            application_id=application_id,
            csrf=csrf,
        )
        content_hashes.append(document["_content_sha256_expected"])

        first = await client.post(
            f"/api/v1/applications/{application_id}/submit",
            headers={"X-CSRF-Token": csrf},
        )
        assert first.status_code == 200, first.text

        second = await client.post(
            f"/api/v1/applications/{application_id}/submit",
            headers={"X-CSRF-Token": csrf},
        )
        assert second.status_code == 409, second.text
        assert second.json()["code"] == "APPLICATION_NOT_DRAFT"
    finally:
        if application_id is not None:
            await _cleanup_submit_artifacts(
                db_conn,
                application_id=application_id,
                content_hashes=content_hashes,
            )
        if policy_document_id is not None:
            await _cleanup_policy_document(db_conn, policy_document_id)
        if csrf:
            await client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
        await _purge_user(db_conn, officer["id"])
        await _purge_user(db_conn, admin["id"])


@pytest.mark.asyncio
async def test_canonical_snapshot_hash_is_deterministic() -> None:
    payload = {
        "application_id": "11111111-1111-1111-1111-111111111111",
        "b": 2,
        "a": 1,
        "document_inventory": [
            {
                "document_id": "22222222-2222-2222-2222-222222222222",
                "document_type": "bank_statement",
                "content_sha256": "a" * 64,
            }
        ],
    }
    first = _canonical_snapshot_hash(payload)
    second = _canonical_snapshot_hash(
        {
            "b": 2,
            "document_inventory": payload["document_inventory"],
            "a": 1,
            "application_id": payload["application_id"],
        }
    )
    assert first == second
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    assert first == hashlib.sha256(encoded.encode("utf-8")).hexdigest()
