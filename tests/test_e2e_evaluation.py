"""End-to-end synthetic loan lifecycle evaluation (M15)."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from psycopg import AsyncConnection

from apps.api.main import app
from apps.api.routers.documents import get_malware_scanner
from modules.governance.verifier import verify_audit_log_integrity
from modules.intake.scanner import MockMalwareScanner
from modules.worker.runner import PLACEHOLDER_PIPELINE, run_workflow
from modules.worker.service import claim_and_prepare
from shared.settings import get_settings
from tests.conftest import _insert_test_user, _purge_user
from tests.test_policies_and_snapshots_schema import _cleanup_policy_document

_PAN_TEXT = """
INCOME TAX DEPARTMENT
GOVT OF INDIA
Permanent Account Number Card
ABCDE1234F
Name: Test Applicant
Date of Birth: 15/08/1990
"""

_SALARY_TEXT = """
Salary Slip
Employer: Acme Corp
Pay Period: January 2026
Basic Pay: 50,000.00
Allowances: 15,000.00
Deductions: 5,000.00
Gross Salary: 65,000.00
Net Salary: 60,000.00
Employee PAN: ABCDE1234F
"""

_POLICY_MARKDOWN = """
## Affordability
FOIR must remain within the configured policy threshold for salaried applicants.

## Documents
Mandatory documents include PAN card and salary slips for identity and income verification.
"""


def _text_pdf(body: str) -> bytes:
    """PDF-magic bytes wrapping UTF-8 text readable by classify/extract nodes."""
    return b"%PDF-1.4\n" + body.strip().encode("utf-8") + b"\n%%EOF\n"


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


async def _resync_audit_chain(db_conn: AsyncConnection[Any]) -> None:
    await db_conn.execute("SELECT set_config('loanlens.allow_audit_append', 'on', true)")
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
    await db_conn.commit()


async def _cleanup_e2e_application(
    db_conn: AsyncConnection[Any],
    application_id: UUID,
    *,
    content_hashes: list[str],
) -> None:
    await db_conn.rollback()

    async def _disable_delete(table: str, trigger: str) -> None:
        await db_conn.execute(f"ALTER TABLE {table} DISABLE TRIGGER {trigger}")
        await db_conn.execute(
            f"DELETE FROM {table} WHERE application_id = %s",
            (application_id,),
        )
        await db_conn.execute(f"ALTER TABLE {table} ENABLE TRIGGER {trigger}")

    await _disable_delete(
        "field_corrections", "field_corrections_append_only_trg"
    )
    await _disable_delete(
        "finding_dispositions", "finding_dispositions_append_only_trg"
    )
    await db_conn.execute(
        "ALTER TABLE assessments DISABLE TRIGGER assessments_decided_freeze_trg"
    )
    await db_conn.execute(
        "DELETE FROM assessments WHERE application_id = %s",
        (application_id,),
    )
    await db_conn.execute(
        "ALTER TABLE assessments ENABLE TRIGGER assessments_decided_freeze_trg"
    )
    await _disable_delete(
        "assessment_ai_observations",
        "assessment_ai_observations_append_only_trg",
    )
    await _disable_delete(
        "application_financial_calculations",
        "application_financial_calculations_append_only_trg",
    )
    await _disable_delete(
        "verification_findings", "verification_findings_append_only_trg"
    )
    await _disable_delete(
        "verification_runs", "verification_runs_append_only_trg"
    )
    await _disable_delete(
        "salary_slip_extractions", "salary_slip_extractions_append_only_trg"
    )
    await _disable_delete(
        "bank_statement_monthly_summary",
        "bank_statement_monthly_summary_append_only_trg",
    )
    await _disable_delete(
        "document_extracted_fields",
        "document_extracted_fields_append_only_trg",
    )
    await db_conn.execute(
        "DELETE FROM document_extractions WHERE application_id = %s",
        (application_id,),
    )
    await _disable_delete(
        "document_failure_events", "document_failure_events_append_only_trg"
    )
    await db_conn.execute(
        "DELETE FROM application_documents WHERE application_id = %s",
        (application_id,),
    )
    await db_conn.execute(
        "DELETE FROM workflow_node_events WHERE application_id = %s",
        (application_id,),
    )
    await db_conn.execute(
        "DELETE FROM workflow_checkpoints WHERE application_id = %s",
        (application_id,),
    )
    await _disable_delete(
        "snapshot_calculation_inputs",
        "snapshot_calculation_inputs_append_only_trg",
    )
    await db_conn.execute(
        "UPDATE processing_runs SET input_snapshot_id = NULL "
        "WHERE application_id = %s",
        (application_id,),
    )
    await _disable_delete(
        "application_input_snapshots",
        "application_input_snapshots_append_only_trg",
    )
    await db_conn.execute(
        "DELETE FROM processing_runs WHERE application_id = %s",
        (application_id,),
    )
    await db_conn.execute("SELECT set_config('loanlens.allow_audit_append', 'on', true)")
    await db_conn.execute(
        "DELETE FROM audit_events WHERE application_id = %s",
        (application_id,),
    )
    await _resync_audit_chain(db_conn)
    await db_conn.execute(
        "UPDATE applications SET applicable_policy_version_id = NULL "
        "WHERE id = %s",
        (application_id,),
    )
    await _disable_delete(
        "application_state_history",
        "application_state_history_append_only_trg",
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
async def test_full_loan_lifecycle_evaluation(
    client: AsyncClient,
    db_conn: AsyncConnection[Any],
) -> None:
    officer = await _insert_test_user(
        db_conn,
        email="e2e.officer@example.com",
        full_name="E2E Loan Officer",
        role="loan_officer",
        password="officer-password-not-a-secret",
    )
    reviewer = await _insert_test_user(
        db_conn,
        email="e2e.reviewer@example.com",
        full_name="E2E Reviewer",
        role="reviewer",
        password="reviewer-password-not-a-secret",
    )
    admin = await _insert_test_user(
        db_conn,
        email="e2e.admin@example.com",
        full_name="E2E Admin",
        role="admin",
        password="admin-password-not-a-secret",
    )

    pool = app.state.pool
    assert pool is not None

    policy_document_id: UUID | None = None
    application_id: UUID | None = None
    content_hashes: list[str] = []
    admin_csrf = ""
    officer_csrf = ""
    reviewer_csrf = ""

    try:
        # 1. Admin activates policy v1.0 with PAN + salary requirements + chunks.
        admin_csrf = await _login(
            client, email=admin["email"], password=admin["password"]
        )
        today = datetime.now(timezone.utc).date()
        doc = await client.post(
            "/api/v1/policies",
            headers={"X-CSRF-Token": admin_csrf},
            json={
                "title": "E2E Evaluation Policy",
                "lender_label": "synthetic_demo",
            },
        )
        assert doc.status_code == 201, doc.text
        policy_document_id = UUID(doc.json()["id"])

        version = await client.post(
            f"/api/v1/policies/{policy_document_id}/versions",
            headers={"X-CSRF-Token": admin_csrf},
            json={
                "version_label": "v1.0",
                "annual_interest_rate_pct": "10.50",
                "effective_from": (today - timedelta(days=1)).isoformat(),
                "effective_to": (today + timedelta(days=365)).isoformat(),
                "thresholds": [
                    {
                        "applicant_type": "salaried",
                        "min_monthly_income": "25000.00",
                        "max_foir_pct": "50.00",
                        "min_employment_months": 12,
                        "max_loan_amount": "2500000.00",
                    }
                ],
                "doc_requirements": [
                    {
                        "applicant_type": "salaried",
                        "document_type": "pan_card",
                        "is_mandatory": True,
                    },
                    {
                        "applicant_type": "salaried",
                        "document_type": "salary_slip",
                        "is_mandatory": True,
                    },
                ],
            },
        )
        assert version.status_code == 201, version.text
        policy_version_id = UUID(version.json()["id"])

        chunks = await client.post(
            f"/api/v1/policies/versions/{policy_version_id}/chunks",
            headers={"X-CSRF-Token": admin_csrf},
            json={"markdown_text": _POLICY_MARKDOWN},
        )
        assert chunks.status_code == 201, chunks.text

        activate = await client.post(
            f"/api/v1/policies/versions/{policy_version_id}/activate",
            headers={"X-CSRF-Token": admin_csrf},
        )
        assert activate.status_code == 200, activate.text
        assert activate.json()["status"] == "active"
        assert activate.json()["version_label"] == "v1.0"

        await client.post(
            "/api/v1/auth/logout",
            headers={"X-CSRF-Token": admin_csrf},
        )
        admin_csrf = ""

        # 2. Loan Officer creates application and uploads PAN + salary slip.
        officer_csrf = await _login(
            client, email=officer["email"], password=officer["password"]
        )
        create = await client.post(
            "/api/v1/applications",
            headers={"X-CSRF-Token": officer_csrf},
            json={
                "applicant_type": "salaried",
                "requested_amount": "500000.00",
                "tenure_months": 36,
                "loan_purpose": "home_renovation",
                "applicant": {
                    "full_name": "Test Applicant",
                    "date_of_birth": "1990-08-15",
                    "pan": "ABCDE1234F",
                    "current_address": "12 Example Road, Bhubaneswar",
                    "employer_or_business_name": "Acme Corp",
                    "time_in_job_or_business_months": 24,
                    "gross_monthly_income": "65000.00",
                    "net_monthly_income": "60000.00",
                    "total_existing_monthly_emis": "5000.00",
                },
            },
        )
        assert create.status_code == 201, create.text
        application_id = UUID(create.json()["id"])
        assert create.json()["status"] == "draft"

        pan_bytes = _text_pdf(_PAN_TEXT)
        pan_upload = await client.post(
            f"/api/v1/applications/{application_id}/documents",
            headers={"X-CSRF-Token": officer_csrf},
            files={"file": ("pan_card.pdf", pan_bytes, "application/pdf")},
            data={"document_type": "pan_card"},
        )
        assert pan_upload.status_code == 201, pan_upload.text
        content_hashes.append(hashlib.sha256(pan_bytes).hexdigest())

        salary_bytes = _text_pdf(_SALARY_TEXT)
        salary_upload = await client.post(
            f"/api/v1/applications/{application_id}/documents",
            headers={"X-CSRF-Token": officer_csrf},
            files={"file": ("salary_slip.pdf", salary_bytes, "application/pdf")},
            data={"document_type": "salary_slip"},
        )
        assert salary_upload.status_code == 201, salary_upload.text
        content_hashes.append(hashlib.sha256(salary_bytes).hexdigest())

        # 3. Loan Officer submits application to the processing queue.
        submit = await client.post(
            f"/api/v1/applications/{application_id}/submit",
            headers={"X-CSRF-Token": officer_csrf},
        )
        assert submit.status_code == 200, submit.text
        assert submit.json()["status"] == "submitted"
        assert submit.json()["applicable_policy_version_id"] == str(policy_version_id)

        await client.post(
            "/api/v1/auth/logout",
            headers={"X-CSRF-Token": officer_csrf},
        )
        officer_csrf = ""

        run_row = await db_conn.execute(
            """
            SELECT id
              FROM processing_runs
             WHERE application_id = %s
             ORDER BY created_at DESC
             LIMIT 1
            """,
            (application_id,),
        )
        run = await run_row.fetchone()
        assert run is not None
        await db_conn.execute(
            """
            UPDATE processing_runs
               SET created_at = %s
             WHERE id = %s
            """,
            (datetime(2000, 1, 1, tzinfo=timezone.utc), run["id"]),
        )
        await db_conn.commit()

        # 4. Background worker claims and runs classify→…→observe.
        async with pool.connection() as conn:
            async with conn.transaction():
                claim = await claim_and_prepare(
                    conn,
                    worker_id="e2e-evaluation-worker",
                    lease_seconds=120,
                )
        assert claim is not None
        assert claim.id == run["id"]

        terminal = await run_workflow(
            pool,
            claim,
            PLACEHOLDER_PIPELINE,
            request_id="e2e-evaluation-run",
        )
        assert terminal == "succeeded"

        status_row = await db_conn.execute(
            "SELECT status::text AS status FROM applications WHERE id = %s",
            (application_id,),
        )
        status = await status_row.fetchone()
        assert status is not None
        assert status["status"] == "awaiting_human_review"

        findings_row = await db_conn.execute(
            """
            SELECT id, rule_code, status::text AS status
              FROM verification_findings
             WHERE application_id = %s
             ORDER BY rule_code ASC
            """,
            (application_id,),
        )
        findings = [dict(row) for row in await findings_row.fetchall()]
        assert len(findings) >= 11

        calc_row = await db_conn.execute(
            """
            SELECT 1 AS present
              FROM application_financial_calculations
             WHERE application_id = %s
             LIMIT 1
            """,
            (application_id,),
        )
        assert await calc_row.fetchone() is not None

        obs_row = await db_conn.execute(
            """
            SELECT 1 AS present
              FROM assessment_ai_observations
             WHERE application_id = %s
             LIMIT 1
            """,
            (application_id,),
        )
        assert await obs_row.fetchone() is not None

        # 5–6. Reviewer claims, disposes a non-pass finding if present, approves.
        reviewer_csrf = await _login(
            client, email=reviewer["email"], password=reviewer["password"]
        )
        claim_resp = await client.post(
            f"/api/v1/applications/{application_id}/claim",
            headers={"X-CSRF-Token": reviewer_csrf},
        )
        assert claim_resp.status_code == 201, claim_resp.text
        assessment = claim_resp.json()
        assessment_id = UUID(assessment["id"])
        assert assessment["decided_at"] is None

        in_review = await db_conn.execute(
            "SELECT status::text AS status FROM applications WHERE id = %s",
            (application_id,),
        )
        in_review_row = await in_review.fetchone()
        assert in_review_row is not None
        assert in_review_row["status"] == "in_review"

        actionable = next(
            (
                finding
                for finding in findings
                if finding["status"] in {"flagged", "failed"}
            ),
            None,
        )
        if actionable is not None:
            disposition = await client.post(
                (
                    f"/api/v1/applications/{application_id}/assessments/"
                    f"{assessment_id}/dispositions"
                ),
                headers={"X-CSRF-Token": reviewer_csrf},
                json={
                    "finding_id": str(actionable["id"]),
                    "action": "accepted_risk",
                    "justification": "Accepted residual verification variance for e2e",
                },
            )
            assert disposition.status_code == 201, disposition.text

        decide = await client.post(
            (
                f"/api/v1/applications/{application_id}/assessments/"
                f"{assessment_id}/decide"
            ),
            headers={"X-CSRF-Token": reviewer_csrf},
            json={
                "recommendation": "approve",
                "reviewer_notes": "E2E evaluation approve after full pipeline review",
            },
        )
        assert decide.status_code == 200, decide.text
        decided = decide.json()
        assert decided["recommendation"] == "approve"
        assert decided["decided_at"] is not None

        # 7. Application reaches review_completed; assessment is frozen.
        completed = await db_conn.execute(
            "SELECT status::text AS status FROM applications WHERE id = %s",
            (application_id,),
        )
        completed_row = await completed.fetchone()
        assert completed_row is not None
        assert completed_row["status"] == "review_completed"

        redecide = await client.post(
            (
                f"/api/v1/applications/{application_id}/assessments/"
                f"{assessment_id}/decide"
            ),
            headers={"X-CSRF-Token": reviewer_csrf},
            json={
                "recommendation": "reject",
                "reviewer_notes": "Must not mutate a decided assessment in e2e",
            },
        )
        assert redecide.status_code == 409, redecide.text

        # 8. Reviewer exports the traceable PDF assessment report.
        export = await client.get(
            f"/api/v1/applications/{application_id}/assessment/export"
        )
        assert export.status_code == 200, export.text
        assert export.headers["content-type"].startswith("application/pdf")
        assert export.content[:5] == b"%PDF-"
        assert len(export.content) > 500

        await client.post(
            "/api/v1/auth/logout",
            headers={"X-CSRF-Token": reviewer_csrf},
        )
        reviewer_csrf = ""

        # 9. Cryptographic audit chain must remain fully intact.
        report = await verify_audit_log_integrity(db_conn)
        assert report.is_valid is True, report.error_message
        assert report.tampered_event_id is None
        assert report.error_message is None
        assert report.total_events_checked > 0

        admin_csrf = await _login(
            client, email=admin["email"], password=admin["password"]
        )
        verify_api = await client.get("/api/v1/audit/verify")
        assert verify_api.status_code == 200, verify_api.text
        assert verify_api.json()["is_valid"] is True
    finally:
        if application_id is not None:
            await _cleanup_e2e_application(
                db_conn,
                application_id,
                content_hashes=content_hashes,
            )
        if policy_document_id is not None:
            await _cleanup_policy_document(db_conn, policy_document_id)
        for csrf in (reviewer_csrf, officer_csrf, admin_csrf):
            if csrf:
                await client.post(
                    "/api/v1/auth/logout",
                    headers={"X-CSRF-Token": csrf},
                )
        await _purge_user(db_conn, officer["id"])
        await _purge_user(db_conn, reviewer["id"])
        await _purge_user(db_conn, admin["id"])
