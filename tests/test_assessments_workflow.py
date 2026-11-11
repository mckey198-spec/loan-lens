"""API tests for credit reviewer claim/disposition/correction/decide (M12-T03)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from psycopg import AsyncConnection
from psycopg.errors import InsufficientPrivilege
from psycopg.types.json import Jsonb

from apps.api.main import app


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


async def _cleanup_review_rows(
    db_conn: AsyncConnection[Any],
    application_id: UUID,
) -> None:
    await db_conn.execute(
        "ALTER TABLE field_corrections "
        "DISABLE TRIGGER field_corrections_append_only_trg"
    )
    await db_conn.execute(
        "DELETE FROM field_corrections WHERE application_id = %s",
        (application_id,),
    )
    await db_conn.execute(
        "ALTER TABLE field_corrections "
        "ENABLE TRIGGER field_corrections_append_only_trg"
    )
    await db_conn.execute(
        "ALTER TABLE finding_dispositions "
        "DISABLE TRIGGER finding_dispositions_append_only_trg"
    )
    await db_conn.execute(
        "DELETE FROM finding_dispositions WHERE application_id = %s",
        (application_id,),
    )
    await db_conn.execute(
        "ALTER TABLE finding_dispositions "
        "ENABLE TRIGGER finding_dispositions_append_only_trg"
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
    await db_conn.commit()


async def _cleanup_application(
    db_conn: AsyncConnection[Any],
    application_id: UUID,
    *,
    policy_document_id: UUID | None = None,
    policy_version_id: UUID | None = None,
    stored_file_id: UUID | None = None,
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
    await _cleanup_review_rows(db_conn, application_id)

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
        "ALTER TABLE document_extracted_fields "
        "DISABLE TRIGGER document_extracted_fields_append_only_trg"
    )
    await db_conn.execute(
        "DELETE FROM document_extracted_fields WHERE application_id = %s",
        (application_id,),
    )
    await db_conn.execute(
        "ALTER TABLE document_extracted_fields "
        "ENABLE TRIGGER document_extracted_fields_append_only_trg"
    )
    await db_conn.execute(
        "DELETE FROM document_extractions WHERE application_id = %s",
        (application_id,),
    )
    await db_conn.execute(
        "DELETE FROM application_documents WHERE application_id = %s",
        (application_id,),
    )

    await db_conn.execute(
        "UPDATE processing_runs SET input_snapshot_id = NULL "
        "WHERE application_id = %s",
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

    if stored_file_id is not None:
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


async def _seed_awaiting_review(
    db_conn: AsyncConnection[Any],
    *,
    officer_user_id: UUID,
) -> dict[str, UUID]:
    now = datetime.now(timezone.utc)
    ref = f"LL-ARW-{uuid4().hex[:8].upper()}"
    content_hash = uuid4().hex + uuid4().hex  # 64 hex chars, unique per seed

    app_row = await db_conn.execute(
        """
        INSERT INTO applications (
            reference_code, status, created_by_user_id, applicant_type,
            requested_amount, currency, tenure_months, loan_purpose,
            created_at, updated_at
        ) VALUES (
            %s, 'draft'::application_status, %s, 'salaried'::applicant_type,
            %s, 'INR', 36, 'home_renovation', %s, %s
        )
        RETURNING id
        """,
        (ref, officer_user_id, Decimal("500000.00"), now, now),
    )
    application = await app_row.fetchone()
    assert application is not None
    application_id = application["id"]

    await db_conn.execute(
        """
        INSERT INTO applicants (
            application_id, full_name, date_of_birth, pan, current_address,
            employer_or_business_name, time_in_job_or_business_months,
            gross_monthly_income, net_monthly_income,
            total_existing_monthly_emis, created_at, updated_at
        ) VALUES (
            %s, 'Synthetic Applicant', %s, 'ABCDE1234F', '1 Test Lane',
            'Acme Corp', 24, %s, %s, %s, %s, %s
        )
        """,
        (
            application_id,
            date(1990, 1, 1),
            Decimal("75000.00"),
            Decimal("60000.00"),
            Decimal("5000.00"),
            now,
            now,
        ),
    )

    policy_doc = await db_conn.execute(
        """
        INSERT INTO policy_documents (title, lender_label, created_by_user_id, created_at)
        VALUES (%s, %s, %s, %s)
        RETURNING id
        """,
        ("Workflow Policy", "synthetic_demo", officer_user_id, now),
    )
    policy_document = await policy_doc.fetchone()
    assert policy_document is not None
    policy_document_id = policy_document["id"]

    policy_ver = await db_conn.execute(
        """
        INSERT INTO policy_versions (
            policy_document_id, version_label, status, annual_interest_rate_pct,
            effective_from, effective_to, created_by_user_id, created_at, updated_at
        ) VALUES (
            %s, %s, 'draft'::policy_status, %s, %s, %s, %s, %s, %s
        )
        RETURNING id
        """,
        (
            policy_document_id,
            f"v-{uuid4().hex[:6]}",
            Decimal("10.50"),
            date(2026, 1, 1),
            date(2026, 12, 31),
            officer_user_id,
            now,
            now,
        ),
    )
    policy_version = await policy_ver.fetchone()
    assert policy_version is not None
    policy_version_id = policy_version["id"]

    await db_conn.execute(
        """
        UPDATE applications
           SET applicable_policy_version_id = %s
         WHERE id = %s
        """,
        (policy_version_id, application_id),
    )

    snapshot = await db_conn.execute(
        """
        INSERT INTO application_input_snapshots (
            application_id, snapshot_number, snapshot_hash, as_of_date,
            applicant_type, requested_amount, tenure_months, loan_purpose,
            declared_gross_monthly_income, declared_net_monthly_income,
            declared_existing_emis, document_inventory_json,
            applicable_policy_version_id, created_by_user_id, created_at
        ) VALUES (
            %s, 1, %s, %s, 'salaried'::applicant_type, %s, 36, 'home_renovation',
            %s, %s, %s, '[]'::jsonb, %s, %s, %s
        )
        RETURNING id
        """,
        (
            application_id,
            content_hash,
            date(2026, 1, 15),
            Decimal("500000.00"),
            Decimal("75000.00"),
            Decimal("60000.00"),
            Decimal("5000.00"),
            policy_version_id,
            officer_user_id,
            now,
        ),
    )
    snapshot_row = await snapshot.fetchone()
    assert snapshot_row is not None
    snapshot_id = snapshot_row["id"]

    run = await db_conn.execute(
        """
        INSERT INTO processing_runs (
            application_id, input_snapshot_id, status, attempt_number,
            trigger, fence_token, finished_at, created_at
        ) VALUES (
            %s, %s, 'succeeded'::processing_run_status, 1, 'submit', 0, %s, %s
        )
        RETURNING id
        """,
        (application_id, snapshot_id, now, now),
    )
    run_row = await run.fetchone()
    assert run_row is not None
    run_id = run_row["id"]

    vrun = await db_conn.execute(
        """
        INSERT INTO verification_runs (
            application_id, processing_run_id, input_snapshot_id,
            total_rules_evaluated, passed_count, flagged_count, failed_count,
            created_at
        ) VALUES (%s, %s, %s, 1, 0, 1, 0, %s)
        RETURNING id
        """,
        (application_id, run_id, snapshot_id, now),
    )
    vrun_row = await vrun.fetchone()
    assert vrun_row is not None
    verification_run_id = vrun_row["id"]

    finding = await db_conn.execute(
        """
        INSERT INTO verification_findings (
            application_id, verification_run_id, rule_code, rule_name,
            status, severity, message, diff_payload_json,
            provenance_sources_json, claim_fence, created_at
        ) VALUES (
            %s, %s, 'BR-05', 'Salary Credit',
            'flagged'::rule_status, 'medium'::rule_severity,
            'Salary credit mismatch', %s, %s, 0, %s
        )
        RETURNING id
        """,
        (application_id, verification_run_id, Jsonb({}), Jsonb([]), now),
    )
    finding_row = await finding.fetchone()
    assert finding_row is not None
    finding_id = finding_row["id"]

    stored = await db_conn.execute(
        """
        INSERT INTO stored_files (
            content_sha256, storage_uri, original_filename, mime_type,
            byte_size, signature_ok, malware_scanner, malware_scanned_at,
            malware_signature_version, created_at
        ) VALUES (
            %s, %s, 'slip.pdf', 'application/pdf', 1024, true, 'clamav',
            %s, 'daily.cvd', %s
        )
        RETURNING id
        """,
        (content_hash, f"file:///{content_hash[:16]}", now, now),
    )
    stored_row = await stored.fetchone()
    assert stored_row is not None
    stored_file_id = stored_row["id"]

    doc = await db_conn.execute(
        """
        INSERT INTO application_documents (
            application_id, stored_file_id, content_sha256, document_type,
            status, uploaded_by_user_id, created_at, updated_at
        ) VALUES (
            %s, %s, %s, 'salary_slip'::document_type,
            'uploaded'::document_status, %s, %s, %s
        )
        RETURNING id
        """,
        (
            application_id,
            stored_file_id,
            content_hash,
            officer_user_id,
            now,
            now,
        ),
    )
    doc_row = await doc.fetchone()
    assert doc_row is not None
    document_id = doc_row["id"]

    extraction = await db_conn.execute(
        """
        INSERT INTO document_extractions (
            application_id, application_document_id, processing_run_id,
            extractor_name, extractor_version, status, raw_payload_json,
            created_at, updated_at
        ) VALUES (
            %s, %s, %s, 'fake_extractor', '1.0.0',
            'completed'::extraction_status, '{}'::jsonb, %s, %s
        )
        RETURNING id
        """,
        (application_id, document_id, run_id, now, now),
    )
    extraction_row = await extraction.fetchone()
    assert extraction_row is not None

    field = await db_conn.execute(
        """
        INSERT INTO document_extracted_fields (
            application_id, extraction_id, field_name, raw_value, created_at
        ) VALUES (
            %s, %s, 'net_salary'::extraction_field_name, '55000', %s
        )
        RETURNING id
        """,
        (application_id, extraction_row["id"], now),
    )
    field_row = await field.fetchone()
    assert field_row is not None
    extracted_field_id = field_row["id"]

    # Legal path: draft → submitted → processing → awaiting_human_review.
    for from_status, to_status in (
        ("draft", "submitted"),
        ("submitted", "processing"),
        ("processing", "awaiting_human_review"),
    ):
        await db_conn.execute(
            """
            INSERT INTO application_state_history (
                application_id, from_status, to_status, actor_type,
                actor_user_id, reason, created_at
            ) VALUES (
                %s, %s::application_status, %s::application_status,
                'system'::audit_actor_type, NULL,
                %s, %s
            )
            """,
            (
                application_id,
                from_status,
                to_status,
                f"Synthetic seed {from_status}->{to_status}",
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

    return {
        "application_id": application_id,
        "policy_document_id": policy_document_id,
        "policy_version_id": policy_version_id,
        "snapshot_id": snapshot_id,
        "processing_run_id": run_id,
        "finding_id": finding_id,
        "extracted_field_id": extracted_field_id,
        "stored_file_id": stored_file_id,
    }


async def _app_status(
    db_conn: AsyncConnection[Any],
    application_id: UUID,
) -> str:
    row = await db_conn.execute(
        "SELECT status::text AS status FROM applications WHERE id = %s",
        (application_id,),
    )
    result = await row.fetchone()
    assert result is not None
    return result["status"]


@pytest.mark.asyncio
async def test_reviewer_claim_disposition_correction_and_decide(
    client: AsyncClient,
    test_user: dict[str, Any],
    reviewer_user: dict[str, Any],
    db_conn: AsyncConnection[Any],
) -> None:
    seeded = await _seed_awaiting_review(
        db_conn, officer_user_id=test_user["id"]
    )
    application_id = seeded["application_id"]
    try:
        csrf = await _login(
            client,
            email=reviewer_user["email"],
            password=reviewer_user["password"],
        )

        claim = await client.post(
            f"/api/v1/applications/{application_id}/claim",
            headers={"X-CSRF-Token": csrf},
        )
        assert claim.status_code == 201, claim.text
        assessment = claim.json()
        assessment_id = UUID(assessment["id"])
        assert assessment["application_id"] == str(application_id)
        assert assessment["processing_run_id"] == str(seeded["processing_run_id"])
        assert assessment["input_snapshot_id"] == str(seeded["snapshot_id"])
        assert assessment["applicable_policy_version_id"] == str(
            seeded["policy_version_id"]
        )
        assert assessment["assigned_reviewer_id"] == str(reviewer_user["id"])
        assert assessment["recommendation"] is None
        assert assessment["decided_at"] is None
        assert await _app_status(db_conn, application_id) == "in_review"

        audit_claim = await db_conn.execute(
            """
            SELECT action FROM audit_events
             WHERE application_id = %s AND action = 'application.claimed'
            """,
            (application_id,),
        )
        assert await audit_claim.fetchone() is not None

        disposition = await client.post(
            (
                f"/api/v1/applications/{application_id}/assessments/"
                f"{assessment_id}/dispositions"
            ),
            headers={"X-CSRF-Token": csrf},
            json={
                "finding_id": str(seeded["finding_id"]),
                "action": "accepted_risk",
                "justification": "Accepted residual salary credit variance",
            },
        )
        assert disposition.status_code == 201, disposition.text
        disp_body = disposition.json()
        assert disp_body["action"] == "accepted_risk"
        assert disp_body["verification_finding_id"] == str(seeded["finding_id"])
        assert len(disp_body["justification"]) >= 10

        correction = await client.post(
            (
                f"/api/v1/applications/{application_id}/assessments/"
                f"{assessment_id}/corrections"
            ),
            headers={"X-CSRF-Token": csrf},
            json={
                "extracted_field_id": str(seeded["extracted_field_id"]),
                "corrected_value_numeric": "57500.00",
                "reason": "Corrected net salary after slip re-read",
            },
        )
        assert correction.status_code == 201, correction.text
        corr_body = correction.json()
        assert corr_body["extracted_field_id"] == str(seeded["extracted_field_id"])
        assert Decimal(corr_body["corrected_value_numeric"]) == Decimal("57500.00")
        assert corr_body["assessment_id"] == str(assessment_id)

        decide = await client.post(
            (
                f"/api/v1/applications/{application_id}/assessments/"
                f"{assessment_id}/decide"
            ),
            headers={"X-CSRF-Token": csrf},
            json={
                "recommendation": "approve",
                "reviewer_notes": "Affordability and identity checks are acceptable",
            },
        )
        assert decide.status_code == 200, decide.text
        decided = decide.json()
        assert decided["recommendation"] == "approve"
        assert decided["decided_at"] is not None
        assert await _app_status(db_conn, application_id) == "review_completed"

        listing = await client.get(
            f"/api/v1/applications/{application_id}/assessments"
        )
        assert listing.status_code == 200, listing.text
        list_body = listing.json()
        assert list_body["total"] == 1
        assert list_body["items"][0]["id"] == str(assessment_id)
        assert list_body["items"][0]["decided_at"] is not None

        # Decided assessment is frozen (API conflict + DB trigger).
        redecide = await client.post(
            (
                f"/api/v1/applications/{application_id}/assessments/"
                f"{assessment_id}/decide"
            ),
            headers={"X-CSRF-Token": csrf},
            json={
                "recommendation": "reject",
                "reviewer_notes": "Attempt to change a decided assessment",
            },
        )
        assert redecide.status_code == 409, redecide.text
        assert redecide.json()["code"] in {
            "APPLICATION_INVALID_STATE",
            "ASSESSMENT_ALREADY_DECIDED",
        }

        with pytest.raises(InsufficientPrivilege):
            await db_conn.execute(
                """
                UPDATE assessments
                   SET reviewer_notes = 'late mutation attempt'
                 WHERE id = %s
                """,
                (assessment_id,),
            )
            await db_conn.commit()
        await db_conn.rollback()
    finally:
        await _cleanup_application(
            db_conn,
            application_id,
            policy_document_id=seeded["policy_document_id"],
            policy_version_id=seeded["policy_version_id"],
            stored_file_id=seeded["stored_file_id"],
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("recommendation", "expected_status"),
    [
        ("approve", "review_completed"),
        ("exception_approved", "review_completed"),
        ("reject", "review_completed"),
        ("refer", "escalated"),
    ],
)
async def test_decide_maps_recommendation_to_terminal_status(
    client: AsyncClient,
    test_user: dict[str, Any],
    reviewer_user: dict[str, Any],
    db_conn: AsyncConnection[Any],
    recommendation: str,
    expected_status: str,
) -> None:
    seeded = await _seed_awaiting_review(
        db_conn, officer_user_id=test_user["id"]
    )
    application_id = seeded["application_id"]
    try:
        csrf = await _login(
            client,
            email=reviewer_user["email"],
            password=reviewer_user["password"],
        )
        claim = await client.post(
            f"/api/v1/applications/{application_id}/claim",
            headers={"X-CSRF-Token": csrf},
        )
        assert claim.status_code == 201, claim.text
        assessment_id = claim.json()["id"]

        decide = await client.post(
            (
                f"/api/v1/applications/{application_id}/assessments/"
                f"{assessment_id}/decide"
            ),
            headers={"X-CSRF-Token": csrf},
            json={
                "recommendation": recommendation,
                "reviewer_notes": f"Decision notes for {recommendation} path",
            },
        )
        assert decide.status_code == 200, decide.text
        body = decide.json()
        assert body["recommendation"] == recommendation
        assert body["decided_at"] is not None
        assert await _app_status(db_conn, application_id) == expected_status
    finally:
        await _cleanup_application(
            db_conn,
            application_id,
            policy_document_id=seeded["policy_document_id"],
            policy_version_id=seeded["policy_version_id"],
            stored_file_id=seeded["stored_file_id"],
        )


@pytest.mark.asyncio
async def test_loan_officer_forbidden_on_all_review_mutations(
    client: AsyncClient,
    test_user: dict[str, Any],
    reviewer_user: dict[str, Any],
    db_conn: AsyncConnection[Any],
) -> None:
    seeded = await _seed_awaiting_review(
        db_conn, officer_user_id=test_user["id"]
    )
    application_id = seeded["application_id"]
    assessment_id: str | None = None
    try:
        reviewer_csrf = await _login(
            client,
            email=reviewer_user["email"],
            password=reviewer_user["password"],
        )
        claim = await client.post(
            f"/api/v1/applications/{application_id}/claim",
            headers={"X-CSRF-Token": reviewer_csrf},
        )
        assert claim.status_code == 201, claim.text
        assessment_id = claim.json()["id"]

        # Fresh client session as loan officer.
        officer_client = AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        )
        async with officer_client:
            officer_csrf = await _login(
                officer_client,
                email=test_user["email"],
                password=test_user["password"],
            )

            claim_forbidden = await officer_client.post(
                f"/api/v1/applications/{application_id}/claim",
                headers={"X-CSRF-Token": officer_csrf},
            )
            assert claim_forbidden.status_code == 403, claim_forbidden.text
            assert claim_forbidden.json()["code"] == "AUTH_FORBIDDEN"

            # Need another awaiting app for a pure claim-forbidden check.
            # For dispositions/corrections/decide against the claimed assessment:
            disposition = await officer_client.post(
                (
                    f"/api/v1/applications/{application_id}/assessments/"
                    f"{assessment_id}/dispositions"
                ),
                headers={"X-CSRF-Token": officer_csrf},
                json={
                    "finding_id": str(seeded["finding_id"]),
                    "action": "overridden",
                    "justification": "Officer attempt to dispose finding",
                },
            )
            assert disposition.status_code == 403, disposition.text
            assert disposition.json()["code"] == "AUTH_FORBIDDEN"

            correction = await officer_client.post(
                (
                    f"/api/v1/applications/{application_id}/assessments/"
                    f"{assessment_id}/corrections"
                ),
                headers={"X-CSRF-Token": officer_csrf},
                json={
                    "extracted_field_id": str(seeded["extracted_field_id"]),
                    "corrected_value_text": "tampered",
                    "reason": "Officer attempt to correct extracted field",
                },
            )
            assert correction.status_code == 403, correction.text
            assert correction.json()["code"] == "AUTH_FORBIDDEN"

            decide = await officer_client.post(
                (
                    f"/api/v1/applications/{application_id}/assessments/"
                    f"{assessment_id}/decide"
                ),
                headers={"X-CSRF-Token": officer_csrf},
                json={
                    "recommendation": "approve",
                    "reviewer_notes": "Officer attempt to decide assessment",
                },
            )
            assert decide.status_code == 403, decide.text
            assert decide.json()["code"] == "AUTH_FORBIDDEN"

        # Fresh awaiting application: officer cannot claim before reviewer does.
        seeded2 = await _seed_awaiting_review(
            db_conn, officer_user_id=test_user["id"]
        )
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as officer_client2:
                officer_csrf2 = await _login(
                    officer_client2,
                    email=test_user["email"],
                    password=test_user["password"],
                )
                claim2 = await officer_client2.post(
                    f"/api/v1/applications/{seeded2['application_id']}/claim",
                    headers={"X-CSRF-Token": officer_csrf2},
                )
                assert claim2.status_code == 403, claim2.text
                assert claim2.json()["code"] == "AUTH_FORBIDDEN"
                assert (
                    await _app_status(db_conn, seeded2["application_id"])
                    == "awaiting_human_review"
                )
        finally:
            await _cleanup_application(
                db_conn,
                seeded2["application_id"],
                policy_document_id=seeded2["policy_document_id"],
                policy_version_id=seeded2["policy_version_id"],
                stored_file_id=seeded2["stored_file_id"],
            )
    finally:
        await _cleanup_application(
            db_conn,
            application_id,
            policy_document_id=seeded["policy_document_id"],
            policy_version_id=seeded["policy_version_id"],
            stored_file_id=seeded["stored_file_id"],
        )


@pytest.mark.asyncio
async def test_disposition_rejects_short_justification(
    client: AsyncClient,
    test_user: dict[str, Any],
    reviewer_user: dict[str, Any],
    db_conn: AsyncConnection[Any],
) -> None:
    seeded = await _seed_awaiting_review(
        db_conn, officer_user_id=test_user["id"]
    )
    application_id = seeded["application_id"]
    try:
        csrf = await _login(
            client,
            email=reviewer_user["email"],
            password=reviewer_user["password"],
        )
        claim = await client.post(
            f"/api/v1/applications/{application_id}/claim",
            headers={"X-CSRF-Token": csrf},
        )
        assert claim.status_code == 201, claim.text
        assessment_id = claim.json()["id"]

        short = await client.post(
            (
                f"/api/v1/applications/{application_id}/assessments/"
                f"{assessment_id}/dispositions"
            ),
            headers={"X-CSRF-Token": csrf},
            json={
                "finding_id": str(seeded["finding_id"]),
                "action": "false_positive",
                "justification": "too short",
            },
        )
        assert short.status_code == 422, short.text
    finally:
        await _cleanup_application(
            db_conn,
            application_id,
            policy_document_id=seeded["policy_document_id"],
            policy_version_id=seeded["policy_version_id"],
            stored_file_id=seeded["stored_file_id"],
        )
