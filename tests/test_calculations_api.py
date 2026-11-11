"""API tests for financial calculations query endpoints (M09-T03)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from psycopg import AsyncConnection

from apps.api.main import app
from modules.calculations import repository as calc_repository
from modules.calculations.engine import compute_financial_metrics
from modules.calculations.schemas import FinancialCalculationRecord
from modules.calculations.service import (
    fetch_calculation_inputs,
    run_financial_calculation,
)
from tests.conftest import _insert_test_user, _purge_user

_HASH_A = "a" * 64
_HASH_B = "b" * 64


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


async def _cleanup_financial_calculations(
    db_conn: AsyncConnection[Any],
    application_id: UUID,
) -> None:
    await db_conn.execute(
        "ALTER TABLE application_financial_calculations "
        "DISABLE TRIGGER application_financial_calculations_append_only_trg"
    )
    await db_conn.execute(
        "DELETE FROM application_financial_calculations "
        "WHERE application_id = %s",
        (application_id,),
    )
    await db_conn.execute(
        "ALTER TABLE application_financial_calculations "
        "ENABLE TRIGGER application_financial_calculations_append_only_trg"
    )
    await db_conn.commit()


async def _cleanup_application(
    db_conn: AsyncConnection[Any],
    application_id: UUID,
    *,
    policy_version_id: UUID | None = None,
    policy_document_id: UUID | None = None,
) -> None:
    await _cleanup_financial_calculations(db_conn, application_id)
    await db_conn.execute(
        "UPDATE processing_runs SET input_snapshot_id = NULL "
        "WHERE application_id = %s",
        (application_id,),
    )
    await db_conn.execute(
        "ALTER TABLE snapshot_calculation_inputs "
        "DISABLE TRIGGER snapshot_calculation_inputs_append_only_trg"
    )
    await db_conn.execute(
        "DELETE FROM snapshot_calculation_inputs WHERE application_id = %s",
        (application_id,),
    )
    await db_conn.execute(
        "ALTER TABLE snapshot_calculation_inputs "
        "ENABLE TRIGGER snapshot_calculation_inputs_append_only_trg"
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
        "DELETE FROM processing_runs WHERE application_id = %s",
        (application_id,),
    )
    await db_conn.execute(
        "UPDATE applications SET applicable_policy_version_id = NULL "
        "WHERE id = %s",
        (application_id,),
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
    await db_conn.commit()

    if policy_version_id is not None:
        await db_conn.execute(
            "DELETE FROM policy_threshold_configs "
            "WHERE policy_version_id = %s",
            (policy_version_id,),
        )
        await db_conn.execute(
            "ALTER TABLE policy_versions "
            "DISABLE TRIGGER policy_versions_active_freeze_trg"
        )
        await db_conn.execute(
            "DELETE FROM policy_versions WHERE id = %s",
            (policy_version_id,),
        )
        await db_conn.execute(
            "ALTER TABLE policy_versions "
            "ENABLE TRIGGER policy_versions_active_freeze_trg"
        )
        await db_conn.commit()
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
        ("Calculations API Test Policy", "synthetic_demo", admin_user_id, now),
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
            f"LL-CAPI-{uuid4().hex[:10].upper()}",
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
            "Calc API Test Applicant",
            date(1990, 8, 15),
            "ABCDE1234F",
            "123 Example Street",
            "Acme Corp",
            Decimal("65000.00"),
            Decimal("60000.00"),
            Decimal("5000.00"),
            now,
            now,
        ),
    )
    await db_conn.commit()
    return application_id


async def _insert_snapshot_inputs_and_run(
    db_conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    policy_version_id: UUID,
    created_by_user_id: UUID,
    snapshot_number: int,
    snapshot_hash: str,
    claim_fence: int,
    monthly_net_income: Decimal = Decimal("60000.00"),
    monthly_obligations: Decimal = Decimal("5000.00"),
    requested_loan_amount: Decimal = Decimal("500000.00"),
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
            %s, %s, %s, %s, 'salaried'::applicant_type,
            %s, 36, 'home_renovation',
            %s, %s, %s, '[]'::jsonb, %s, %s, %s
        )
        RETURNING id
        """,
        (
            application_id,
            snapshot_number,
            snapshot_hash,
            date(2026, 1, 15),
            requested_loan_amount,
            monthly_net_income + Decimal("15000.00"),
            monthly_net_income,
            monthly_obligations,
            policy_version_id,
            created_by_user_id,
            now,
        ),
    )
    snap_row = await snap.fetchone()
    assert snap_row is not None
    snapshot_id = snap_row["id"]

    await db_conn.execute(
        """
        INSERT INTO snapshot_calculation_inputs (
            application_id,
            input_snapshot_id,
            monthly_gross_income,
            monthly_net_income,
            monthly_obligations,
            requested_loan_amount,
            tenure_months,
            annual_interest_rate_pct,
            max_allowed_foir_pct,
            created_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, 36, %s, %s, %s
        )
        """,
        (
            application_id,
            snapshot_id,
            monthly_net_income + Decimal("15000.00"),
            monthly_net_income,
            monthly_obligations,
            requested_loan_amount,
            Decimal("10.50"),
            Decimal("50.00"),
            now,
        ),
    )

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
            %s, %s, 'running'::processing_run_status, %s, 'submit',
            %s, 'worker-calc-api-test', %s, %s, %s, %s
        )
        RETURNING id
        """,
        (
            application_id,
            snapshot_id,
            snapshot_number,
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


async def _seed_calculation_fixture(
    db_conn: AsyncConnection[Any],
    *,
    officer_user_id: UUID,
    admin_user_id: UUID,
    with_second_run: bool = False,
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
    snapshot_id, processing_run_id = await _insert_snapshot_inputs_and_run(
        db_conn,
        application_id=application_id,
        policy_version_id=policy_version_id,
        created_by_user_id=officer_user_id,
        snapshot_number=1,
        snapshot_hash=_HASH_A,
        claim_fence=1,
    )
    async with db_conn.transaction():
        _inputs, _result, record = await run_financial_calculation(
            db_conn,
            application_id=application_id,
            processing_run_id=processing_run_id,
            input_snapshot_id=snapshot_id,
            claim_fence=1,
        )

    second_run_id: UUID | None = None
    second_record_id: UUID | None = None
    if with_second_run:
        # Only one active (queued/running) run per application is allowed.
        await db_conn.execute(
            """
            UPDATE processing_runs
               SET status = 'succeeded'::processing_run_status,
                   finished_at = %s
             WHERE id = %s
            """,
            (datetime.now(timezone.utc), processing_run_id),
        )
        await db_conn.commit()

        later = record.created_at + timedelta(seconds=5)
        snapshot_id_2, second_run_id = await _insert_snapshot_inputs_and_run(
            db_conn,
            application_id=application_id,
            policy_version_id=policy_version_id,
            created_by_user_id=officer_user_id,
            snapshot_number=2,
            snapshot_hash=_HASH_B,
            claim_fence=2,
            monthly_net_income=Decimal("70000.00"),
            monthly_obligations=Decimal("4000.00"),
            requested_loan_amount=Decimal("400000.00"),
        )
        async with db_conn.transaction():
            inputs_2 = await fetch_calculation_inputs(db_conn, snapshot_id_2)
            result_2 = compute_financial_metrics(inputs_2)
            row_2 = await calc_repository.record_financial_calculation(
                db_conn,
                application_id,
                second_run_id,
                snapshot_id_2,
                result_2,
                2,
                created_at=later,
            )
        record_2 = FinancialCalculationRecord.model_validate(row_2)
        second_record_id = record_2.id

    return {
        "application_id": application_id,
        "policy_document_id": policy_document_id,
        "policy_version_id": policy_version_id,
        "processing_run_id": processing_run_id,
        "calculation_id": record.id,
        "record": record,
        "second_run_id": second_run_id,
        "second_calculation_id": second_record_id,
    }


@pytest.mark.asyncio
async def test_authorized_roles_can_fetch_latest_calculation(
    client: AsyncClient,
    test_user: dict[str, Any],
    reviewer_user: dict[str, Any],
    admin_user: dict[str, Any],
    db_conn: AsyncConnection[Any],
) -> None:
    fixture = await _seed_calculation_fixture(
        db_conn,
        officer_user_id=test_user["id"],
        admin_user_id=admin_user["id"],
    )
    application_id = fixture["application_id"]
    path = f"/api/v1/applications/{application_id}/calculations"
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
                body = response.json()
                assert body["id"] == str(fixture["calculation_id"])
                assert body["application_id"] == str(application_id)
                assert body["processing_run_id"] == str(
                    fixture["processing_run_id"]
                )
                assert body["proposed_emi"] == "16251.22"
                assert body["foir_passed"] is True
                assert "calculation_details" in body
                assert isinstance(body["calculation_details"], dict)
    finally:
        await _cleanup_application(
            db_conn,
            application_id,
            policy_version_id=fixture["policy_version_id"],
            policy_document_id=fixture["policy_document_id"],
        )


@pytest.mark.asyncio
async def test_specific_run_lookup_matches_processing_run(
    client: AsyncClient,
    test_user: dict[str, Any],
    admin_user: dict[str, Any],
    db_conn: AsyncConnection[Any],
) -> None:
    fixture = await _seed_calculation_fixture(
        db_conn,
        officer_user_id=test_user["id"],
        admin_user_id=admin_user["id"],
        with_second_run=True,
    )
    application_id = fixture["application_id"]
    try:
        await _login(
            client,
            email=test_user["email"],
            password=test_user["password"],
        )

        first = await client.get(
            f"/api/v1/applications/{application_id}/calculations/runs/"
            f"{fixture['processing_run_id']}"
        )
        assert first.status_code == 200, first.text
        assert first.json()["id"] == str(fixture["calculation_id"])
        assert first.json()["processing_run_id"] == str(
            fixture["processing_run_id"]
        )
        assert first.json()["proposed_emi"] == "16251.22"

        second = await client.get(
            f"/api/v1/applications/{application_id}/calculations/runs/"
            f"{fixture['second_run_id']}"
        )
        assert second.status_code == 200, second.text
        assert second.json()["id"] == str(fixture["second_calculation_id"])
        assert second.json()["processing_run_id"] == str(
            fixture["second_run_id"]
        )
        assert second.json()["id"] != first.json()["id"]

        latest = await client.get(
            f"/api/v1/applications/{application_id}/calculations"
        )
        assert latest.status_code == 200, latest.text
        assert latest.json()["id"] == str(fixture["second_calculation_id"])
        assert latest.json()["processing_run_id"] == str(
            fixture["second_run_id"]
        )
    finally:
        await _cleanup_application(
            db_conn,
            application_id,
            policy_version_id=fixture["policy_version_id"],
            policy_document_id=fixture["policy_document_id"],
        )


@pytest.mark.asyncio
async def test_mismatched_application_id_returns_404(
    client: AsyncClient,
    test_user: dict[str, Any],
    admin_user: dict[str, Any],
    db_conn: AsyncConnection[Any],
) -> None:
    fixture = await _seed_calculation_fixture(
        db_conn,
        officer_user_id=test_user["id"],
        admin_user_id=admin_user["id"],
    )
    other_admin = await _insert_test_user(
        db_conn,
        email="calc.api.other.admin@example.com",
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
            f"/api/v1/applications/{uuid4()}/calculations"
        )
        assert missing_app.status_code == 404, missing_app.text
        assert missing_app.json()["code"] == "FINANCIAL_CALCULATION_NOT_FOUND"

        no_calcs = await client.get(
            f"/api/v1/applications/{other_application_id}/calculations"
        )
        assert no_calcs.status_code == 404, no_calcs.text
        assert no_calcs.json()["code"] == "FINANCIAL_CALCULATION_NOT_FOUND"

        mismatched = await client.get(
            f"/api/v1/applications/{other_application_id}/calculations/runs/"
            f"{fixture['processing_run_id']}"
        )
        assert mismatched.status_code == 404, mismatched.text
        assert mismatched.json()["code"] == "FINANCIAL_CALCULATION_NOT_FOUND"

        unknown_run = await client.get(
            f"/api/v1/applications/{fixture['application_id']}/calculations/"
            f"runs/{uuid4()}"
        )
        assert unknown_run.status_code == 404, unknown_run.text
        assert unknown_run.json()["code"] == "FINANCIAL_CALCULATION_NOT_FOUND"
    finally:
        await _cleanup_application(
            db_conn,
            fixture["application_id"],
            policy_version_id=fixture["policy_version_id"],
            policy_document_id=fixture["policy_document_id"],
        )
        if other_application_id is not None:
            await _cleanup_application(
                db_conn,
                other_application_id,
                policy_version_id=other_policy_version_id,
                policy_document_id=other_policy_document_id,
            )
        await _purge_user(db_conn, other_admin["id"])


@pytest.mark.asyncio
async def test_unauthenticated_calculations_returns_401(
    client: AsyncClient,
    test_user: dict[str, Any],
    admin_user: dict[str, Any],
    db_conn: AsyncConnection[Any],
) -> None:
    fixture = await _seed_calculation_fixture(
        db_conn,
        officer_user_id=test_user["id"],
        admin_user_id=admin_user["id"],
    )
    application_id = fixture["application_id"]
    try:
        latest = await client.get(
            f"/api/v1/applications/{application_id}/calculations"
        )
        assert latest.status_code == 401, latest.text

        by_run = await client.get(
            f"/api/v1/applications/{application_id}/calculations/runs/"
            f"{fixture['processing_run_id']}"
        )
        assert by_run.status_code == 401, by_run.text
    finally:
        await _cleanup_application(
            db_conn,
            application_id,
            policy_version_id=fixture["policy_version_id"],
            policy_document_id=fixture["policy_document_id"],
        )
