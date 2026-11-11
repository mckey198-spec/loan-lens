"""API tests for verification findings query endpoints (M08-T03)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from apps.api.main import app
from modules.verification.schemas import RuleEvaluationResult
from modules.verification.service import persist_verification_results
from tests.conftest import _insert_test_user, _purge_user

_HASH_A = "a" * 64


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


async def _cleanup_verification_fixture(
    db_conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    policy_version_id: UUID | None = None,
    policy_document_id: UUID | None = None,
) -> None:
    await db_conn.execute(
        "ALTER TABLE verification_findings "
        "DISABLE TRIGGER verification_findings_append_only_trg"
    )
    await db_conn.execute(
        "DELETE FROM verification_findings WHERE application_id = %s",
        (application_id,),
    )
    await db_conn.execute(
        "ALTER TABLE verification_findings "
        "ENABLE TRIGGER verification_findings_append_only_trg"
    )
    await db_conn.execute(
        "ALTER TABLE verification_runs "
        "DISABLE TRIGGER verification_runs_append_only_trg"
    )
    await db_conn.execute(
        "DELETE FROM verification_runs WHERE application_id = %s",
        (application_id,),
    )
    await db_conn.execute(
        "ALTER TABLE verification_runs "
        "ENABLE TRIGGER verification_runs_append_only_trg"
    )
    await db_conn.execute(
        "DELETE FROM processing_runs WHERE application_id = %s",
        (application_id,),
    )
    await db_conn.execute(
        "ALTER TABLE application_input_snapshots "
        "DISABLE TRIGGER application_input_snapshots_append_only_trg"
    )
    await db_conn.execute(
        "DELETE FROM application_input_snapshots WHERE application_id = %s",
        (application_id,),
    )
    await db_conn.execute(
        "ALTER TABLE application_input_snapshots "
        "ENABLE TRIGGER application_input_snapshots_append_only_trg"
    )
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
    if policy_version_id is not None:
        await db_conn.execute(
            "DELETE FROM policy_document_requirements WHERE policy_version_id = %s",
            (policy_version_id,),
        )
        await db_conn.execute(
            "DELETE FROM policy_threshold_configs WHERE policy_version_id = %s",
            (policy_version_id,),
        )
        await db_conn.execute(
            "DELETE FROM policy_versions WHERE id = %s",
            (policy_version_id,),
        )
    if policy_document_id is not None:
        await db_conn.execute(
            "DELETE FROM policy_documents WHERE id = %s",
            (policy_document_id,),
        )
    await db_conn.commit()


async def _seed_policy(
    db_conn: AsyncConnection[Any],
    *,
    admin_user_id: UUID,
) -> tuple[UUID, UUID]:
    now = datetime.now(timezone.utc)
    doc = await db_conn.execute(
        """
        INSERT INTO policy_documents (
            title, lender_label, created_by_user_id, created_at
        ) VALUES (%s, %s, %s, %s)
        RETURNING id
        """,
        ("Verification API Test Policy", "synthetic_demo", admin_user_id, now),
    )
    doc_row = await doc.fetchone()
    assert doc_row is not None
    policy_document_id = doc_row["id"]

    ver = await db_conn.execute(
        """
        INSERT INTO policy_versions (
            policy_document_id,
            version_label,
            status,
            annual_interest_rate_pct,
            effective_from,
            effective_to,
            created_by_user_id,
            created_at,
            updated_at
        ) VALUES (
            %s, %s, 'draft'::policy_status, %s,
            %s, %s, %s, %s, %s
        )
        RETURNING id
        """,
        (
            policy_document_id,
            f"v-{uuid4().hex[:8]}",
            Decimal("10.50"),
            date(2026, 1, 1),
            date(2026, 12, 31),
            admin_user_id,
            now,
            now,
        ),
    )
    ver_row = await ver.fetchone()
    assert ver_row is not None
    await db_conn.commit()
    return policy_document_id, ver_row["id"]


async def _insert_application(
    db_conn: AsyncConnection[Any],
    *,
    created_by_user_id: UUID,
    policy_version_id: UUID,
) -> UUID:
    now = datetime.now(timezone.utc)
    app = await db_conn.execute(
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
            applicable_policy_version_id,
            created_at,
            updated_at
        ) VALUES (
            %s, 'draft'::application_status, %s,
            'salaried'::applicant_type, %s, 'INR', 36,
            'home_renovation', %s, %s, %s
        )
        RETURNING id
        """,
        (
            f"LL-VAPI-{uuid4().hex[:10].upper()}",
            created_by_user_id,
            Decimal("500000.00"),
            policy_version_id,
            now,
            now,
        ),
    )
    app_row = await app.fetchone()
    assert app_row is not None
    application_id = app_row["id"]
    await db_conn.execute(
        """
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
            total_existing_monthly_emis,
            created_at,
            updated_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, 24,
            %s, %s, %s, %s, %s
        )
        """,
        (
            application_id,
            "API Test Applicant",
            date(1990, 8, 15),
            "ABCDE1234F",
            "123 Example Street",
            "Acme Corp",
            Decimal("65000.00"),
            Decimal("60000.00"),
            Decimal("0.00"),
            now,
            now,
        ),
    )
    await db_conn.commit()
    return application_id


async def _insert_snapshot_and_run(
    db_conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    policy_version_id: UUID,
    created_by_user_id: UUID,
    claim_fence: int = 1,
) -> tuple[UUID, UUID]:
    now = datetime.now(timezone.utc)
    snap = await db_conn.execute(
        """
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
            document_inventory_json,
            applicable_policy_version_id,
            created_by_user_id,
            created_at
        ) VALUES (
            %s, 1, %s, %s, 'salaried'::applicant_type,
            %s, 36, 'home_renovation',
            %s, %s, %s, %s, %s, %s, %s
        )
        RETURNING id
        """,
        (
            application_id,
            _HASH_A,
            date(2026, 1, 15),
            Decimal("500000.00"),
            Decimal("65000.00"),
            Decimal("60000.00"),
            Decimal("0.00"),
            Jsonb([{"document_type": "pan_card"}]),
            policy_version_id,
            created_by_user_id,
            now,
        ),
    )
    snap_row = await snap.fetchone()
    assert snap_row is not None
    snapshot_id = snap_row["id"]

    run = await db_conn.execute(
        """
        INSERT INTO processing_runs (
            application_id,
            input_snapshot_id,
            status,
            attempt_number,
            trigger,
            fence_token,
            locked_by,
            locked_at,
            lease_expires_at,
            started_at,
            created_at
        ) VALUES (
            %s, %s, 'running'::processing_run_status, 1, 'submit',
            %s, 'worker-verify-api-test', %s, %s, %s, %s
        )
        RETURNING id
        """,
        (
            application_id,
            snapshot_id,
            claim_fence,
            now,
            now + timedelta(minutes=10),
            now,
            now,
        ),
    )
    run_row = await run.fetchone()
    assert run_row is not None
    await db_conn.commit()
    return snapshot_id, run_row["id"]


def _sample_results() -> list[RuleEvaluationResult]:
    return [
        RuleEvaluationResult(
            rule_code="BR-01",
            rule_name="Applicant name consistency",
            status="passed",
            severity="info",
            message="Declared name matches extracted name.",
            diff_payload={"ratio": "1.00"},
            provenance_sources=[{"layer": "normalized", "field": "applicant_name"}],
        ),
        RuleEvaluationResult(
            rule_code="BR-02",
            rule_name="PAN consistency",
            status="flagged",
            severity="medium",
            message="PAN format matched with minor discrepancy.",
            diff_payload={"declared": "ABCDE1234F", "extracted": "ABCDE1234F"},
            provenance_sources=[{"layer": "normalized", "field": "pan"}],
        ),
        RuleEvaluationResult(
            rule_code="BR-08",
            rule_name="Minimum income",
            status="failed",
            severity="high",
            message="Declared income below policy minimum.",
            diff_payload={"declared": "20000.00", "minimum": "25000.00"},
            provenance_sources=[{"layer": "policy"}],
        ),
    ]


async def _seed_verification_run(
    db_conn: AsyncConnection[Any],
    *,
    officer_user_id: UUID,
    admin_user_id: UUID,
) -> dict[str, Any]:
    policy_document_id, policy_version_id = await _seed_policy(
        db_conn,
        admin_user_id=admin_user_id,
    )
    application_id = await _insert_application(
        db_conn,
        created_by_user_id=officer_user_id,
        policy_version_id=policy_version_id,
    )
    snapshot_id, processing_run_id = await _insert_snapshot_and_run(
        db_conn,
        application_id=application_id,
        policy_version_id=policy_version_id,
        created_by_user_id=officer_user_id,
    )
    results = _sample_results()
    async with db_conn.transaction():
        run, findings = await persist_verification_results(
            db_conn,
            application_id=application_id,
            processing_run_id=processing_run_id,
            input_snapshot_id=snapshot_id,
            results=results,
            claim_fence=1,
        )
    return {
        "application_id": application_id,
        "policy_document_id": policy_document_id,
        "policy_version_id": policy_version_id,
        "processing_run_id": processing_run_id,
        "verification_run_id": run.id,
        "run": run,
        "findings": findings,
        "results": results,
    }


@pytest.mark.asyncio
async def test_latest_verification_returns_summary_and_findings(
    client: AsyncClient,
    test_user: dict[str, Any],
    admin_user: dict[str, Any],
    db_conn: AsyncConnection[Any],
) -> None:
    fixture = await _seed_verification_run(
        db_conn,
        officer_user_id=test_user["id"],
        admin_user_id=admin_user["id"],
    )
    application_id = fixture["application_id"]
    try:
        await _login(
            client,
            email=test_user["email"],
            password=test_user["password"],
        )
        response = await client.get(
            f"/api/v1/applications/{application_id}/verification"
        )
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["id"] == str(fixture["verification_run_id"])
        assert body["application_id"] == str(application_id)
        assert body["processing_run_id"] == str(fixture["processing_run_id"])
        assert body["total_rules_evaluated"] == 3
        assert body["passed_count"] == 1
        assert body["flagged_count"] == 1
        assert body["failed_count"] == 1
        assert len(body["findings"]) == 3
        assert [item["rule_code"] for item in body["findings"]] == [
            "BR-01",
            "BR-02",
            "BR-08",
        ]
        assert body["findings"][0]["diff_payload"] == {"ratio": "1.00"}
        assert body["findings"][1]["provenance_sources"] == [
            {"layer": "normalized", "field": "pan"}
        ]

        by_id = await client.get(
            f"/api/v1/applications/{application_id}/verification/runs/"
            f"{fixture['verification_run_id']}"
        )
        assert by_id.status_code == 200, by_id.text
        assert by_id.json()["id"] == body["id"]
        assert len(by_id.json()["findings"]) == 3
    finally:
        await _cleanup_verification_fixture(
            db_conn,
            application_id=application_id,
            policy_version_id=fixture["policy_version_id"],
            policy_document_id=fixture["policy_document_id"],
        )


@pytest.mark.asyncio
async def test_authorized_roles_can_read_verification(
    client: AsyncClient,
    test_user: dict[str, Any],
    reviewer_user: dict[str, Any],
    admin_user: dict[str, Any],
    db_conn: AsyncConnection[Any],
) -> None:
    fixture = await _seed_verification_run(
        db_conn,
        officer_user_id=test_user["id"],
        admin_user_id=admin_user["id"],
    )
    application_id = fixture["application_id"]
    path = f"/api/v1/applications/{application_id}/verification"
    try:
        for user in (test_user, reviewer_user, admin_user):
            role_client = AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            )
            async with role_client:
                await _login(
                    role_client,
                    email=user["email"],
                    password=user["password"],
                )
                response = await role_client.get(path)
                assert response.status_code == 200, (
                    f"{user['role']}: {response.text}"
                )
                assert response.json()["total_rules_evaluated"] == 3
    finally:
        await _cleanup_verification_fixture(
            db_conn,
            application_id=application_id,
            policy_version_id=fixture["policy_version_id"],
            policy_document_id=fixture["policy_document_id"],
        )


@pytest.mark.asyncio
async def test_missing_application_or_mismatched_run_returns_404(
    client: AsyncClient,
    test_user: dict[str, Any],
    admin_user: dict[str, Any],
    db_conn: AsyncConnection[Any],
) -> None:
    fixture = await _seed_verification_run(
        db_conn,
        officer_user_id=test_user["id"],
        admin_user_id=admin_user["id"],
    )
    other_admin = await _insert_test_user(
        db_conn,
        email="verify.api.other.admin@example.com",
        full_name="Other Admin",
        role="admin",
        password="other-admin-password-not-a-secret",
    )
    other_policy_document_id: UUID | None = None
    other_policy_version_id: UUID | None = None
    other_application_id: UUID | None = None
    try:
        other_policy_document_id, other_policy_version_id = await _seed_policy(
            db_conn,
            admin_user_id=other_admin["id"],
        )
        other_application_id = await _insert_application(
            db_conn,
            created_by_user_id=test_user["id"],
            policy_version_id=other_policy_version_id,
        )

        await _login(
            client,
            email=test_user["email"],
            password=test_user["password"],
        )

        missing_app = await client.get(
            f"/api/v1/applications/{uuid4()}/verification"
        )
        assert missing_app.status_code == 404, missing_app.text
        assert missing_app.json()["code"] == "VERIFICATION_RUN_NOT_FOUND"

        no_runs = await client.get(
            f"/api/v1/applications/{other_application_id}/verification"
        )
        assert no_runs.status_code == 404, no_runs.text
        assert no_runs.json()["code"] == "VERIFICATION_RUN_NOT_FOUND"

        mismatched = await client.get(
            f"/api/v1/applications/{other_application_id}/verification/runs/"
            f"{fixture['verification_run_id']}"
        )
        assert mismatched.status_code == 404, mismatched.text
        assert mismatched.json()["code"] == "VERIFICATION_RUN_NOT_FOUND"

        unknown_run = await client.get(
            f"/api/v1/applications/{fixture['application_id']}/verification/runs/"
            f"{uuid4()}"
        )
        assert unknown_run.status_code == 404, unknown_run.text
        assert unknown_run.json()["code"] == "VERIFICATION_RUN_NOT_FOUND"
    finally:
        await _cleanup_verification_fixture(
            db_conn,
            application_id=fixture["application_id"],
            policy_version_id=fixture["policy_version_id"],
            policy_document_id=fixture["policy_document_id"],
        )
        if other_application_id is not None:
            await _cleanup_verification_fixture(
                db_conn,
                application_id=other_application_id,
                policy_version_id=other_policy_version_id,
                policy_document_id=other_policy_document_id,
            )
        await _purge_user(db_conn, other_admin["id"])


@pytest.mark.asyncio
async def test_unauthenticated_verification_returns_401(
    client: AsyncClient,
    test_user: dict[str, Any],
    admin_user: dict[str, Any],
    db_conn: AsyncConnection[Any],
) -> None:
    fixture = await _seed_verification_run(
        db_conn,
        officer_user_id=test_user["id"],
        admin_user_id=admin_user["id"],
    )
    application_id = fixture["application_id"]
    try:
        latest = await client.get(
            f"/api/v1/applications/{application_id}/verification"
        )
        assert latest.status_code == 401, latest.text

        by_id = await client.get(
            f"/api/v1/applications/{application_id}/verification/runs/"
            f"{fixture['verification_run_id']}"
        )
        assert by_id.status_code == 401, by_id.text
    finally:
        await _cleanup_verification_fixture(
            db_conn,
            application_id=application_id,
            policy_version_id=fixture["policy_version_id"],
            policy_document_id=fixture["policy_document_id"],
        )
