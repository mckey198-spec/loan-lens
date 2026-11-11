"""Schema tests for assessments, dispositions, and field corrections (M12-T01)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from psycopg import AsyncConnection
from psycopg.errors import CheckViolation, ForeignKeyViolation, InsufficientPrivilege
from psycopg.types.json import Jsonb

from tests.conftest import _insert_test_user, _purge_user

_HASH_A = "a" * 64
_HASH_B = "b" * 64


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


async def _cleanup_verification(
    db_conn: AsyncConnection[Any],
    application_id: UUID,
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
    await db_conn.commit()


async def _cleanup_extractions(
    db_conn: AsyncConnection[Any],
    application_id: UUID,
) -> None:
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
    await db_conn.commit()


async def _cleanup_snapshots(
    db_conn: AsyncConnection[Any],
    application_id: UUID,
) -> None:
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
    await db_conn.commit()


async def _cleanup_processing(
    db_conn: AsyncConnection[Any],
    application_id: UUID,
) -> None:
    await db_conn.execute(
        "DELETE FROM workflow_node_events WHERE application_id = %s",
        (application_id,),
    )
    await db_conn.execute(
        "DELETE FROM workflow_checkpoints WHERE application_id = %s",
        (application_id,),
    )
    await db_conn.execute(
        "DELETE FROM processing_runs WHERE application_id = %s",
        (application_id,),
    )
    await db_conn.commit()


async def _cleanup_application(
    db_conn: AsyncConnection[Any],
    application_id: UUID,
) -> None:
    await _cleanup_review_rows(db_conn, application_id)
    await _cleanup_verification(db_conn, application_id)
    await _cleanup_extractions(db_conn, application_id)
    await _cleanup_snapshots(db_conn, application_id)
    await _cleanup_processing(db_conn, application_id)
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


async def _cleanup_policy_document(
    db_conn: AsyncConnection[Any],
    policy_document_id: UUID,
) -> None:
    versions = await db_conn.execute(
        "SELECT id FROM policy_versions WHERE policy_document_id = %s",
        (policy_document_id,),
    )
    version_rows = await versions.fetchall()
    for version_id in [row["id"] for row in version_rows]:
        await db_conn.execute(
            "DELETE FROM policy_chunks WHERE policy_version_id = %s",
            (version_id,),
        )
        await db_conn.execute(
            "DELETE FROM policy_document_requirements "
            "WHERE policy_version_id = %s",
            (version_id,),
        )
        await db_conn.execute(
            "DELETE FROM policy_threshold_configs "
            "WHERE policy_version_id = %s",
            (version_id,),
        )
        await db_conn.execute(
            "UPDATE applications SET applicable_policy_version_id = NULL "
            "WHERE applicable_policy_version_id = %s",
            (version_id,),
        )

    await db_conn.execute(
        "ALTER TABLE policy_versions "
        "DISABLE TRIGGER policy_versions_active_freeze_trg"
    )
    await db_conn.execute(
        "DELETE FROM policy_versions WHERE policy_document_id = %s",
        (policy_document_id,),
    )
    await db_conn.execute(
        "ALTER TABLE policy_versions "
        "ENABLE TRIGGER policy_versions_active_freeze_trg"
    )
    await db_conn.execute(
        "DELETE FROM policy_documents WHERE id = %s",
        (policy_document_id,),
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


async def _insert_policy_document(
    db_conn: AsyncConnection[Any],
    *,
    created_by_user_id: UUID,
) -> UUID:
    now = datetime.now(timezone.utc)
    row = await db_conn.execute(
        """
        INSERT INTO policy_documents (
            title,
            lender_label,
            created_by_user_id,
            created_at
        ) VALUES (%s, %s, %s, %s)
        RETURNING id
        """,
        ("Synthetic Retail Policy", "synthetic_demo", created_by_user_id, now),
    )
    result = await row.fetchone()
    assert result is not None
    return result["id"]


async def _insert_policy_version(
    db_conn: AsyncConnection[Any],
    *,
    policy_document_id: UUID,
    created_by_user_id: UUID,
    version_label: str = "v1",
) -> UUID:
    now = datetime.now(timezone.utc)
    row = await db_conn.execute(
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
            %s,
            %s,
            'draft'::policy_status,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s
        )
        RETURNING id
        """,
        (
            policy_document_id,
            version_label,
            Decimal("10.50"),
            date(2026, 1, 1),
            date(2026, 12, 31),
            created_by_user_id,
            now,
            now,
        ),
    )
    result = await row.fetchone()
    assert result is not None
    return result["id"]


async def _insert_snapshot(
    db_conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    policy_version_id: UUID,
    created_by_user_id: UUID,
    snapshot_number: int = 1,
    snapshot_hash: str = _HASH_A,
) -> UUID:
    now = datetime.now(timezone.utc)
    row = await db_conn.execute(
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
            %s, %s, %s, %s,
            'salaried'::applicant_type,
            %s, 36, 'home_renovation',
            %s, %s, %s,
            '[]'::jsonb,
            %s, %s, %s
        )
        RETURNING id
        """,
        (
            application_id,
            snapshot_number,
            snapshot_hash,
            date(2026, 1, 15),
            Decimal("500000.00"),
            Decimal("75000.00"),
            Decimal("60000.00"),
            Decimal("5000.00"),
            policy_version_id,
            created_by_user_id,
            now,
        ),
    )
    result = await row.fetchone()
    assert result is not None
    return result["id"]


async def _insert_processing_run(
    db_conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    input_snapshot_id: UUID,
    status: str = "succeeded",
) -> UUID:
    now = datetime.now(timezone.utc)
    row = await db_conn.execute(
        """
        INSERT INTO processing_runs (
            application_id,
            input_snapshot_id,
            status,
            attempt_number,
            trigger,
            fence_token,
            finished_at,
            created_at
        ) VALUES (
            %s,
            %s,
            %s::processing_run_status,
            1,
            'submit',
            0,
            %s,
            %s
        )
        RETURNING id
        """,
        (application_id, input_snapshot_id, status, now, now),
    )
    result = await row.fetchone()
    assert result is not None
    return result["id"]


async def _insert_verification_run(
    db_conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    processing_run_id: UUID,
    input_snapshot_id: UUID,
) -> UUID:
    now = datetime.now(timezone.utc)
    row = await db_conn.execute(
        """
        INSERT INTO verification_runs (
            application_id,
            processing_run_id,
            input_snapshot_id,
            total_rules_evaluated,
            passed_count,
            flagged_count,
            failed_count,
            created_at
        ) VALUES (
            %s, %s, %s, 1, 0, 1, 0, %s
        )
        RETURNING id
        """,
        (application_id, processing_run_id, input_snapshot_id, now),
    )
    result = await row.fetchone()
    assert result is not None
    return result["id"]


async def _insert_verification_finding(
    db_conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    verification_run_id: UUID,
    rule_code: str = "BR-05",
) -> UUID:
    now = datetime.now(timezone.utc)
    row = await db_conn.execute(
        """
        INSERT INTO verification_findings (
            application_id,
            verification_run_id,
            rule_code,
            rule_name,
            status,
            severity,
            message,
            diff_payload_json,
            provenance_sources_json,
            claim_fence,
            created_at
        ) VALUES (
            %s, %s, %s, %s,
            'flagged'::rule_status,
            'medium'::rule_severity,
            %s,
            %s, %s, 0, %s
        )
        RETURNING id
        """,
        (
            application_id,
            verification_run_id,
            rule_code,
            f"Rule {rule_code}",
            f"Finding for {rule_code}",
            Jsonb({}),
            Jsonb([]),
            now,
        ),
    )
    result = await row.fetchone()
    assert result is not None
    return result["id"]


async def _insert_stored_file(
    db_conn: AsyncConnection[Any],
    *,
    content_sha256: str,
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
            %s, %s, 'slip.pdf', 'application/pdf', 1024, true, 'clamav',
            %s, 'daily.cvd', %s
        )
        RETURNING id
        """,
        (
            content_sha256,
            f"file:///{content_sha256[:16]}",
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
            uploaded_by_user_id,
            created_at,
            updated_at
        ) VALUES (
            %s, %s, %s,
            'salary_slip'::document_type,
            'uploaded'::document_status,
            %s, %s, %s
        )
        RETURNING id
        """,
        (
            application_id,
            stored_file_id,
            content_sha256,
            uploaded_by_user_id,
            now,
            now,
        ),
    )
    result = await row.fetchone()
    assert result is not None
    return result["id"]


async def _insert_extracted_field(
    db_conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    application_document_id: UUID,
    processing_run_id: UUID,
) -> UUID:
    now = datetime.now(timezone.utc)
    extraction = await db_conn.execute(
        """
        INSERT INTO document_extractions (
            application_id,
            application_document_id,
            processing_run_id,
            extractor_name,
            extractor_version,
            status,
            raw_payload_json,
            created_at,
            updated_at
        ) VALUES (
            %s, %s, %s, 'fake_extractor', '1.0.0',
            'completed'::extraction_status, '{}'::jsonb, %s, %s
        )
        RETURNING id
        """,
        (
            application_id,
            application_document_id,
            processing_run_id,
            now,
            now,
        ),
    )
    extraction_row = await extraction.fetchone()
    assert extraction_row is not None

    field = await db_conn.execute(
        """
        INSERT INTO document_extracted_fields (
            application_id,
            extraction_id,
            field_name,
            raw_value,
            created_at
        ) VALUES (
            %s, %s, 'net_salary'::extraction_field_name, '55000', %s
        )
        RETURNING id
        """,
        (application_id, extraction_row["id"], now),
    )
    field_row = await field.fetchone()
    assert field_row is not None
    return field_row["id"]


async def _insert_assessment(
    db_conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    processing_run_id: UUID,
    input_snapshot_id: UUID,
    applicable_policy_version_id: UUID,
    assigned_reviewer_id: UUID | None = None,
    recommendation: str | None = None,
    reviewer_notes: str | None = None,
    decided_at: datetime | None = None,
) -> UUID:
    now = datetime.now(timezone.utc)
    row = await db_conn.execute(
        """
        INSERT INTO assessments (
            application_id,
            processing_run_id,
            input_snapshot_id,
            applicable_policy_version_id,
            assigned_reviewer_id,
            recommendation,
            reviewer_notes,
            decided_at,
            created_at,
            updated_at
        ) VALUES (
            %s, %s, %s, %s, %s,
            %s::assessment_recommendation,
            %s, %s, %s, %s
        )
        RETURNING id
        """,
        (
            application_id,
            processing_run_id,
            input_snapshot_id,
            applicable_policy_version_id,
            assigned_reviewer_id,
            recommendation,
            reviewer_notes,
            decided_at,
            now,
            now,
        ),
    )
    result = await row.fetchone()
    assert result is not None
    return result["id"]


async def _insert_finding_disposition(
    db_conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    assessment_id: UUID,
    verification_finding_id: UUID,
    created_by_user_id: UUID,
    action: str = "accepted_risk",
    justification: str = "Accepted residual risk after review",
) -> UUID:
    row = await db_conn.execute(
        """
        INSERT INTO finding_dispositions (
            application_id,
            assessment_id,
            verification_finding_id,
            action,
            justification,
            created_by_user_id
        ) VALUES (
            %s, %s, %s, %s::finding_disposition_action, %s, %s
        )
        RETURNING id
        """,
        (
            application_id,
            assessment_id,
            verification_finding_id,
            action,
            justification,
            created_by_user_id,
        ),
    )
    result = await row.fetchone()
    assert result is not None
    return result["id"]


async def _insert_field_correction(
    db_conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    extracted_field_id: UUID,
    corrected_by_user_id: UUID,
    assessment_id: UUID | None = None,
    corrected_value_text: str | None = None,
    corrected_value_numeric: Decimal | None = None,
    corrected_value_date: date | None = None,
    reason: str = "Corrected after reviewer inspection",
) -> UUID:
    row = await db_conn.execute(
        """
        INSERT INTO field_corrections (
            application_id,
            extracted_field_id,
            assessment_id,
            corrected_value_text,
            corrected_value_numeric,
            corrected_value_date,
            reason,
            corrected_by_user_id
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s
        )
        RETURNING id
        """,
        (
            application_id,
            extracted_field_id,
            assessment_id,
            corrected_value_text,
            corrected_value_numeric,
            corrected_value_date,
            reason,
            corrected_by_user_id,
        ),
    )
    result = await row.fetchone()
    assert result is not None
    return result["id"]


@pytest.mark.asyncio
async def test_insert_assessment_disposition_and_field_correction(
    db_conn: AsyncConnection[Any],
) -> None:
    user = await _insert_test_user(
        db_conn,
        email="assess.schema.insert@example.com",
        full_name="Assess Insert",
        role="reviewer",
        password="test-password-not-a-secret",
    )
    application_id: UUID | None = None
    policy_document_id: UUID | None = None
    stored_file_id: UUID | None = None
    try:
        application_id = await _insert_draft_application(
            db_conn,
            created_by_user_id=user["id"],
            reference_code=f"LL-ASI-{uuid4().hex[:6].upper()}",
        )
        policy_document_id = await _insert_policy_document(
            db_conn,
            created_by_user_id=user["id"],
        )
        policy_version_id = await _insert_policy_version(
            db_conn,
            policy_document_id=policy_document_id,
            created_by_user_id=user["id"],
        )
        snapshot_id = await _insert_snapshot(
            db_conn,
            application_id=application_id,
            policy_version_id=policy_version_id,
            created_by_user_id=user["id"],
        )
        run_id = await _insert_processing_run(
            db_conn,
            application_id=application_id,
            input_snapshot_id=snapshot_id,
        )
        verification_run_id = await _insert_verification_run(
            db_conn,
            application_id=application_id,
            processing_run_id=run_id,
            input_snapshot_id=snapshot_id,
        )
        finding_id = await _insert_verification_finding(
            db_conn,
            application_id=application_id,
            verification_run_id=verification_run_id,
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
        extracted_field_id = await _insert_extracted_field(
            db_conn,
            application_id=application_id,
            application_document_id=document_id,
            processing_run_id=run_id,
        )

        assessment_id = await _insert_assessment(
            db_conn,
            application_id=application_id,
            processing_run_id=run_id,
            input_snapshot_id=snapshot_id,
            applicable_policy_version_id=policy_version_id,
            assigned_reviewer_id=user["id"],
        )
        disposition_id = await _insert_finding_disposition(
            db_conn,
            application_id=application_id,
            assessment_id=assessment_id,
            verification_finding_id=finding_id,
            created_by_user_id=user["id"],
            action="overridden",
            justification="Overridden with documented compensating control",
        )
        correction_id = await _insert_field_correction(
            db_conn,
            application_id=application_id,
            extracted_field_id=extracted_field_id,
            assessment_id=assessment_id,
            corrected_by_user_id=user["id"],
            corrected_value_numeric=Decimal("56000.00"),
            reason="Typed numeric correction after slip re-read",
        )
        await db_conn.commit()

        assessment = await db_conn.execute(
            """
            SELECT application_id,
                   processing_run_id,
                   input_snapshot_id,
                   applicable_policy_version_id,
                   assigned_reviewer_id,
                   recommendation,
                   decided_at
              FROM assessments
             WHERE id = %s
            """,
            (assessment_id,),
        )
        assessment_row = await assessment.fetchone()
        assert assessment_row is not None
        assert assessment_row["application_id"] == application_id
        assert assessment_row["processing_run_id"] == run_id
        assert assessment_row["input_snapshot_id"] == snapshot_id
        assert assessment_row["applicable_policy_version_id"] == policy_version_id
        assert assessment_row["assigned_reviewer_id"] == user["id"]
        assert assessment_row["recommendation"] is None
        assert assessment_row["decided_at"] is None

        disposition = await db_conn.execute(
            """
            SELECT action::text AS action, justification
              FROM finding_dispositions
             WHERE id = %s
            """,
            (disposition_id,),
        )
        disposition_row = await disposition.fetchone()
        assert disposition_row is not None
        assert disposition_row["action"] == "overridden"
        assert (
            disposition_row["justification"]
            == "Overridden with documented compensating control"
        )

        correction = await db_conn.execute(
            """
            SELECT corrected_value_text,
                   corrected_value_numeric,
                   corrected_value_date,
                   reason
              FROM field_corrections
             WHERE id = %s
            """,
            (correction_id,),
        )
        correction_row = await correction.fetchone()
        assert correction_row is not None
        assert correction_row["corrected_value_text"] is None
        assert correction_row["corrected_value_numeric"] == Decimal("56000.00")
        assert correction_row["corrected_value_date"] is None
        assert correction_row["reason"] == "Typed numeric correction after slip re-read"
    finally:
        await db_conn.rollback()
        if application_id is not None:
            await _cleanup_application(db_conn, application_id)
        if stored_file_id is not None:
            await _cleanup_stored_file(db_conn, stored_file_id)
        if policy_document_id is not None:
            await _cleanup_policy_document(db_conn, policy_document_id)
        await _purge_user(db_conn, user["id"])


@pytest.mark.asyncio
async def test_composite_foreign_keys_enforce_tenant_isolation(
    db_conn: AsyncConnection[Any],
) -> None:
    user = await _insert_test_user(
        db_conn,
        email="assess.schema.fk@example.com",
        full_name="Assess FK",
        role="reviewer",
        password="test-password-not-a-secret",
    )
    application_a: UUID | None = None
    application_b: UUID | None = None
    policy_document_id: UUID | None = None
    try:
        application_a = await _insert_draft_application(
            db_conn,
            created_by_user_id=user["id"],
            reference_code=f"LL-AFA-{uuid4().hex[:6].upper()}",
        )
        application_b = await _insert_draft_application(
            db_conn,
            created_by_user_id=user["id"],
            reference_code=f"LL-AFB-{uuid4().hex[:6].upper()}",
        )
        policy_document_id = await _insert_policy_document(
            db_conn,
            created_by_user_id=user["id"],
        )
        policy_version_id = await _insert_policy_version(
            db_conn,
            policy_document_id=policy_document_id,
            created_by_user_id=user["id"],
        )
        snapshot_a = await _insert_snapshot(
            db_conn,
            application_id=application_a,
            policy_version_id=policy_version_id,
            created_by_user_id=user["id"],
            snapshot_hash=_HASH_A,
        )
        snapshot_b = await _insert_snapshot(
            db_conn,
            application_id=application_b,
            policy_version_id=policy_version_id,
            created_by_user_id=user["id"],
            snapshot_hash=_HASH_B,
        )
        run_a = await _insert_processing_run(
            db_conn,
            application_id=application_a,
            input_snapshot_id=snapshot_a,
        )
        run_b = await _insert_processing_run(
            db_conn,
            application_id=application_b,
            input_snapshot_id=snapshot_b,
        )
        await db_conn.commit()

        with pytest.raises(ForeignKeyViolation):
            await _insert_assessment(
                db_conn,
                application_id=application_a,
                processing_run_id=run_b,
                input_snapshot_id=snapshot_a,
                applicable_policy_version_id=policy_version_id,
            )
            await db_conn.commit()
        await db_conn.rollback()

        with pytest.raises(ForeignKeyViolation):
            await _insert_assessment(
                db_conn,
                application_id=application_a,
                processing_run_id=run_a,
                input_snapshot_id=snapshot_b,
                applicable_policy_version_id=policy_version_id,
            )
            await db_conn.commit()
        await db_conn.rollback()
    finally:
        await db_conn.rollback()
        if application_a is not None:
            await _cleanup_application(db_conn, application_a)
        if application_b is not None:
            await _cleanup_application(db_conn, application_b)
        if policy_document_id is not None:
            await _cleanup_policy_document(db_conn, policy_document_id)
        await _purge_user(db_conn, user["id"])


@pytest.mark.asyncio
async def test_justification_and_reason_require_ten_characters(
    db_conn: AsyncConnection[Any],
) -> None:
    user = await _insert_test_user(
        db_conn,
        email="assess.schema.len@example.com",
        full_name="Assess Length",
        role="reviewer",
        password="test-password-not-a-secret",
    )
    application_id: UUID | None = None
    policy_document_id: UUID | None = None
    stored_file_id: UUID | None = None
    try:
        application_id = await _insert_draft_application(
            db_conn,
            created_by_user_id=user["id"],
            reference_code=f"LL-ASL-{uuid4().hex[:6].upper()}",
        )
        policy_document_id = await _insert_policy_document(
            db_conn,
            created_by_user_id=user["id"],
        )
        policy_version_id = await _insert_policy_version(
            db_conn,
            policy_document_id=policy_document_id,
            created_by_user_id=user["id"],
        )
        snapshot_id = await _insert_snapshot(
            db_conn,
            application_id=application_id,
            policy_version_id=policy_version_id,
            created_by_user_id=user["id"],
        )
        run_id = await _insert_processing_run(
            db_conn,
            application_id=application_id,
            input_snapshot_id=snapshot_id,
        )
        verification_run_id = await _insert_verification_run(
            db_conn,
            application_id=application_id,
            processing_run_id=run_id,
            input_snapshot_id=snapshot_id,
        )
        finding_id = await _insert_verification_finding(
            db_conn,
            application_id=application_id,
            verification_run_id=verification_run_id,
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
        extracted_field_id = await _insert_extracted_field(
            db_conn,
            application_id=application_id,
            application_document_id=document_id,
            processing_run_id=run_id,
        )
        assessment_id = await _insert_assessment(
            db_conn,
            application_id=application_id,
            processing_run_id=run_id,
            input_snapshot_id=snapshot_id,
            applicable_policy_version_id=policy_version_id,
        )
        await db_conn.commit()

        with pytest.raises(CheckViolation):
            await _insert_finding_disposition(
                db_conn,
                application_id=application_id,
                assessment_id=assessment_id,
                verification_finding_id=finding_id,
                created_by_user_id=user["id"],
                justification="too short",
            )
            await db_conn.commit()
        await db_conn.rollback()

        with pytest.raises(CheckViolation):
            await _insert_field_correction(
                db_conn,
                application_id=application_id,
                extracted_field_id=extracted_field_id,
                assessment_id=assessment_id,
                corrected_by_user_id=user["id"],
                corrected_value_text="56000",
                reason="short",
            )
            await db_conn.commit()
        await db_conn.rollback()
    finally:
        await db_conn.rollback()
        if application_id is not None:
            await _cleanup_application(db_conn, application_id)
        if stored_file_id is not None:
            await _cleanup_stored_file(db_conn, stored_file_id)
        if policy_document_id is not None:
            await _cleanup_policy_document(db_conn, policy_document_id)
        await _purge_user(db_conn, user["id"])


@pytest.mark.asyncio
async def test_decided_assessments_reject_updates(
    db_conn: AsyncConnection[Any],
) -> None:
    user = await _insert_test_user(
        db_conn,
        email="assess.schema.freeze@example.com",
        full_name="Assess Freeze",
        role="reviewer",
        password="test-password-not-a-secret",
    )
    application_id: UUID | None = None
    policy_document_id: UUID | None = None
    try:
        application_id = await _insert_draft_application(
            db_conn,
            created_by_user_id=user["id"],
            reference_code=f"LL-ASZ-{uuid4().hex[:6].upper()}",
        )
        policy_document_id = await _insert_policy_document(
            db_conn,
            created_by_user_id=user["id"],
        )
        policy_version_id = await _insert_policy_version(
            db_conn,
            policy_document_id=policy_document_id,
            created_by_user_id=user["id"],
        )
        snapshot_id = await _insert_snapshot(
            db_conn,
            application_id=application_id,
            policy_version_id=policy_version_id,
            created_by_user_id=user["id"],
        )
        run_id = await _insert_processing_run(
            db_conn,
            application_id=application_id,
            input_snapshot_id=snapshot_id,
        )
        assessment_id = await _insert_assessment(
            db_conn,
            application_id=application_id,
            processing_run_id=run_id,
            input_snapshot_id=snapshot_id,
            applicable_policy_version_id=policy_version_id,
        )
        await db_conn.commit()

        # Undecided assessments may still be updated (decision write path).
        decided_at = datetime.now(timezone.utc)
        await db_conn.execute(
            """
            UPDATE assessments
               SET recommendation = 'approve'::assessment_recommendation,
                   decided_at = %s,
                   updated_at = %s
             WHERE id = %s
            """,
            (decided_at, decided_at, assessment_id),
        )
        await db_conn.commit()

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

        retained = await db_conn.execute(
            """
            SELECT recommendation::text AS recommendation, reviewer_notes
              FROM assessments
             WHERE id = %s
            """,
            (assessment_id,),
        )
        row = await retained.fetchone()
        assert row is not None
        assert row["recommendation"] == "approve"
        assert row["reviewer_notes"] is None
    finally:
        await db_conn.rollback()
        if application_id is not None:
            await _cleanup_application(db_conn, application_id)
        if policy_document_id is not None:
            await _cleanup_policy_document(db_conn, policy_document_id)
        await _purge_user(db_conn, user["id"])


@pytest.mark.asyncio
async def test_dispositions_and_corrections_reject_update_and_delete(
    db_conn: AsyncConnection[Any],
) -> None:
    user = await _insert_test_user(
        db_conn,
        email="assess.schema.immut@example.com",
        full_name="Assess Immut",
        role="reviewer",
        password="test-password-not-a-secret",
    )
    application_id: UUID | None = None
    policy_document_id: UUID | None = None
    stored_file_id: UUID | None = None
    try:
        application_id = await _insert_draft_application(
            db_conn,
            created_by_user_id=user["id"],
            reference_code=f"LL-ASM-{uuid4().hex[:6].upper()}",
        )
        policy_document_id = await _insert_policy_document(
            db_conn,
            created_by_user_id=user["id"],
        )
        policy_version_id = await _insert_policy_version(
            db_conn,
            policy_document_id=policy_document_id,
            created_by_user_id=user["id"],
        )
        snapshot_id = await _insert_snapshot(
            db_conn,
            application_id=application_id,
            policy_version_id=policy_version_id,
            created_by_user_id=user["id"],
        )
        run_id = await _insert_processing_run(
            db_conn,
            application_id=application_id,
            input_snapshot_id=snapshot_id,
        )
        verification_run_id = await _insert_verification_run(
            db_conn,
            application_id=application_id,
            processing_run_id=run_id,
            input_snapshot_id=snapshot_id,
        )
        finding_id = await _insert_verification_finding(
            db_conn,
            application_id=application_id,
            verification_run_id=verification_run_id,
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
        extracted_field_id = await _insert_extracted_field(
            db_conn,
            application_id=application_id,
            application_document_id=document_id,
            processing_run_id=run_id,
        )
        assessment_id = await _insert_assessment(
            db_conn,
            application_id=application_id,
            processing_run_id=run_id,
            input_snapshot_id=snapshot_id,
            applicable_policy_version_id=policy_version_id,
        )
        disposition_id = await _insert_finding_disposition(
            db_conn,
            application_id=application_id,
            assessment_id=assessment_id,
            verification_finding_id=finding_id,
            created_by_user_id=user["id"],
        )
        correction_id = await _insert_field_correction(
            db_conn,
            application_id=application_id,
            extracted_field_id=extracted_field_id,
            assessment_id=assessment_id,
            corrected_by_user_id=user["id"],
            corrected_value_text="Corrected Name",
        )
        await db_conn.commit()

        with pytest.raises(InsufficientPrivilege):
            await db_conn.execute(
                """
                UPDATE finding_dispositions
                   SET justification = 'mutated justification text'
                 WHERE id = %s
                """,
                (disposition_id,),
            )
            await db_conn.commit()
        await db_conn.rollback()

        with pytest.raises(InsufficientPrivilege):
            await db_conn.execute(
                "DELETE FROM finding_dispositions WHERE id = %s",
                (disposition_id,),
            )
            await db_conn.commit()
        await db_conn.rollback()

        with pytest.raises(InsufficientPrivilege):
            await db_conn.execute(
                """
                UPDATE field_corrections
                   SET reason = 'mutated correction reason'
                 WHERE id = %s
                """,
                (correction_id,),
            )
            await db_conn.commit()
        await db_conn.rollback()

        with pytest.raises(InsufficientPrivilege):
            await db_conn.execute(
                "DELETE FROM field_corrections WHERE id = %s",
                (correction_id,),
            )
            await db_conn.commit()
        await db_conn.rollback()

        retained_disp = await db_conn.execute(
            "SELECT id FROM finding_dispositions WHERE id = %s",
            (disposition_id,),
        )
        assert await retained_disp.fetchone() is not None

        retained_corr = await db_conn.execute(
            "SELECT id FROM field_corrections WHERE id = %s",
            (correction_id,),
        )
        assert await retained_corr.fetchone() is not None
    finally:
        await db_conn.rollback()
        if application_id is not None:
            await _cleanup_application(db_conn, application_id)
        if stored_file_id is not None:
            await _cleanup_stored_file(db_conn, stored_file_id)
        if policy_document_id is not None:
            await _cleanup_policy_document(db_conn, policy_document_id)
        await _purge_user(db_conn, user["id"])
