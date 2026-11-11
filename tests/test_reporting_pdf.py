"""Assessment PDF export engine and API tests (M13-T01 / Architecture §10)."""

from __future__ import annotations

import re
import zlib
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from modules.reporting.generator import build_assessment_pdf
from modules.reporting.schemas import (
    BR12_ADVISORY_BANNER,
    AiObservationRow,
    AssessmentDecisionBlock,
    AssessmentReportPackage,
    FinancialMetricsBlock,
    RuleFindingRow,
    SnapshotMetadata,
)
from modules.reporting.service import generate_assessment_report


def _pdf_searchable_bytes(pdf_bytes: bytes) -> bytes:
    """Combine raw PDF bytes with zlib-decompressed, unescaped content streams."""
    parts: list[bytes] = [pdf_bytes]
    for match in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", pdf_bytes, re.DOTALL):
        payload = match.group(1)
        if payload.startswith(b"\r\n"):
            payload = payload[2:]
        elif payload.startswith(b"\n"):
            payload = payload[1:]
        try:
            parts.append(zlib.decompress(payload))
        except zlib.error:
            parts.append(payload)
    haystack = b"\n".join(parts)
    # ReportLab escapes parentheses in PDF string literals: \( \)
    return haystack.replace(rb"\(", b"(").replace(rb"\)", b")")


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


async def _cleanup_export_rows(
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
    await db_conn.execute(
        "ALTER TABLE assessment_ai_observations "
        "DISABLE TRIGGER assessment_ai_observations_append_only_trg"
    )
    await db_conn.execute(
        "DELETE FROM assessment_ai_observations WHERE application_id = %s",
        (application_id,),
    )
    await db_conn.execute(
        "ALTER TABLE assessment_ai_observations "
        "ENABLE TRIGGER assessment_ai_observations_append_only_trg"
    )
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
    await _cleanup_export_rows(db_conn, application_id)

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
            "DELETE FROM policy_chunks WHERE policy_version_id = %s",
            (policy_version_id,),
        )
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


async def _seed_exportable_assessment(
    db_conn: AsyncConnection[Any],
    *,
    officer_user_id: UUID,
    reviewer_user_id: UUID,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    ref = f"LL-PDF-{uuid4().hex[:8].upper()}"
    snapshot_hash = uuid4().hex + uuid4().hex
    content_hash = uuid4().hex + uuid4().hex

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
            %s, 'PDF Export Applicant', %s, 'ABCDE1234F', '1 Test Lane',
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
        ("PDF Export Policy", "synthetic_demo", officer_user_id, now),
    )
    policy_document = await policy_doc.fetchone()
    assert policy_document is not None
    policy_document_id = policy_document["id"]

    version_label = f"v-pdf-{uuid4().hex[:6]}"
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
            version_label,
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
        INSERT INTO policy_chunks (
            policy_version_id, chunk_index, section_label, content_text, created_at
        ) VALUES (
            %s, 0, 'FOIR Limit',
            'Maximum FOIR limit for salaried applicants is fifty five percent.',
            %s
        )
        """,
        (policy_version_id, now),
    )

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
            snapshot_hash,
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

    await db_conn.execute(
        """
        INSERT INTO snapshot_calculation_inputs (
            application_id, input_snapshot_id,
            monthly_gross_income, monthly_net_income, monthly_obligations,
            requested_loan_amount, tenure_months, annual_interest_rate_pct,
            max_allowed_foir_pct, created_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, 36, %s, %s, %s
        )
        """,
        (
            application_id,
            snapshot_id,
            Decimal("75000.00"),
            Decimal("60000.00"),
            Decimal("5000.00"),
            Decimal("500000.00"),
            Decimal("10.50"),
            Decimal("55.00"),
            now,
        ),
    )

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

    await db_conn.execute(
        """
        INSERT INTO application_financial_calculations (
            application_id, processing_run_id, input_snapshot_id,
            proposed_emi, total_obligations, foir_pct, foir_threshold_pct,
            foir_passed, net_disposable_income, max_qualifying_loan_amount,
            calculation_details_json, claim_fence, created_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, true, %s, %s, '{}'::jsonb, 0, %s
        )
        """,
        (
            application_id,
            run_id,
            snapshot_id,
            Decimal("16267.00"),
            Decimal("21267.00"),
            Decimal("35.45"),
            Decimal("55.00"),
            Decimal("38733.00"),
            Decimal("750000.00"),
            now,
        ),
    )

    vrun = await db_conn.execute(
        """
        INSERT INTO verification_runs (
            application_id, processing_run_id, input_snapshot_id,
            total_rules_evaluated, passed_count, flagged_count, failed_count,
            created_at
        ) VALUES (%s, %s, %s, 2, 1, 1, 0, %s)
        RETURNING id
        """,
        (application_id, run_id, snapshot_id, now),
    )
    vrun_row = await vrun.fetchone()
    assert vrun_row is not None
    verification_run_id = vrun_row["id"]

    await db_conn.execute(
        """
        INSERT INTO verification_findings (
            application_id, verification_run_id, rule_code, rule_name,
            status, severity, message, diff_payload_json,
            provenance_sources_json, claim_fence, created_at
        ) VALUES (
            %s, %s, 'BR-01', 'Name Match',
            'passed'::rule_status, 'info'::rule_severity,
            'Applicant name matches KYC documents', %s, %s, 0, %s
        )
        """,
        (application_id, verification_run_id, Jsonb({}), Jsonb([]), now),
    )
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
        (
            application_id,
            verification_run_id,
            Jsonb({"declared": "60000", "observed": "57500"}),
            Jsonb([]),
            now,
        ),
    )
    finding_row = await finding.fetchone()
    assert finding_row is not None
    finding_id = finding_row["id"]

    await db_conn.execute(
        """
        INSERT INTO assessment_ai_observations (
            application_id, processing_run_id, model_id, observation_text,
            risk_factors_json, strengths_json, cited_chunk_ids_json,
            is_ai_generated, claim_fence, created_at
        ) VALUES (
            %s, %s, 'synthetic-obs-v1',
            'Cash-flow pattern appears stable with seasonal variance.',
            %s, %s, '[]'::jsonb, true, 0, %s
        )
        """,
        (
            application_id,
            run_id,
            Jsonb(["seasonal variance"]),
            Jsonb(["stable credits"]),
            now,
        ),
    )

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
        (application_id, doc_row["id"], run_id, now, now),
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

    for from_status, to_status in (
        ("draft", "submitted"),
        ("submitted", "processing"),
        ("processing", "awaiting_human_review"),
        ("awaiting_human_review", "in_review"),
        ("in_review", "review_completed"),
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

    assessment = await db_conn.execute(
        """
        INSERT INTO assessments (
            application_id, processing_run_id, input_snapshot_id,
            applicable_policy_version_id, assigned_reviewer_id,
            recommendation, reviewer_notes, decided_at, created_at, updated_at
        ) VALUES (
            %s, %s, %s, %s, %s,
            'approve'::assessment_recommendation,
            'Affordability and identity checks are acceptable',
            %s, %s, %s
        )
        RETURNING id
        """,
        (
            application_id,
            run_id,
            snapshot_id,
            policy_version_id,
            reviewer_user_id,
            now,
            now,
            now,
        ),
    )
    assessment_row = await assessment.fetchone()
    assert assessment_row is not None
    assessment_id = assessment_row["id"]

    await db_conn.execute(
        """
        INSERT INTO finding_dispositions (
            application_id, assessment_id, verification_finding_id,
            action, justification, created_by_user_id, created_at
        ) VALUES (
            %s, %s, %s, 'accepted_risk'::finding_disposition_action,
            'Accepted residual salary credit variance', %s, %s
        )
        """,
        (application_id, assessment_id, finding_id, reviewer_user_id, now),
    )
    await db_conn.execute(
        """
        INSERT INTO field_corrections (
            application_id, extracted_field_id, assessment_id,
            corrected_value_numeric, reason, corrected_by_user_id, created_at
        ) VALUES (
            %s, %s, %s, %s,
            'Corrected net salary after slip re-read', %s, %s
        )
        """,
        (
            application_id,
            field_row["id"],
            assessment_id,
            Decimal("57500.00"),
            reviewer_user_id,
            now,
        ),
    )
    await db_conn.commit()

    return {
        "application_id": application_id,
        "assessment_id": assessment_id,
        "policy_document_id": policy_document_id,
        "policy_version_id": policy_version_id,
        "policy_version_label": version_label,
        "snapshot_id": snapshot_id,
        "snapshot_hash": snapshot_hash,
        "processing_run_id": run_id,
        "finding_id": finding_id,
        "stored_file_id": stored_file_id,
        "reference_code": ref,
    }


def test_build_assessment_pdf_contains_required_markers() -> None:
    snapshot_hash = "a" * 64
    package = AssessmentReportPackage(
        application_id=uuid4(),
        snapshot=SnapshotMetadata(
            snapshot_id=uuid4(),
            snapshot_hash=snapshot_hash,
            as_of_date=date(2026, 1, 15),
            policy_version_id=uuid4(),
            policy_version_label="v-unit-1",
            application_reference_code="LL-UNIT-1",
            application_status="review_completed",
        ),
        decision=AssessmentDecisionBlock(
            assessment_id=uuid4(),
            recommendation="approve",
            reviewer_user_id=uuid4(),
            reviewer_full_name="Unit Reviewer",
            decided_at=datetime(2026, 1, 20, 12, 0, tzinfo=timezone.utc),
            reviewer_notes="Decision notes for unit PDF",
        ),
        financials=FinancialMetricsBlock(
            monthly_gross_income=Decimal("75000.00"),
            monthly_net_income=Decimal("60000.00"),
            monthly_obligations=Decimal("5000.00"),
            proposed_emi=Decimal("16267.00"),
            total_obligations=Decimal("21267.00"),
            foir_pct=Decimal("35.45"),
            foir_threshold_pct=Decimal("55.00"),
            foir_passed=True,
            max_qualifying_loan_amount=Decimal("750000.00"),
            net_disposable_income=Decimal("38733.00"),
        ),
        findings=[
            RuleFindingRow(
                rule_code="BR-01",
                rule_name="Name Match",
                status="passed",
                severity="info",
                message="Name matched",
                diff_payload_json={},
            )
        ],
        ai_observations=[
            AiObservationRow(
                model_id="synthetic-obs-v1",
                observation_text="Advisory note only",
                risk_factors=[],
                strengths=[],
                is_ai_generated=True,
                created_at=datetime(2026, 1, 19, 10, 0, tzinfo=timezone.utc),
            )
        ],
    )

    pdf_bytes = build_assessment_pdf(package)
    haystack = _pdf_searchable_bytes(pdf_bytes)
    assert pdf_bytes.startswith(b"%PDF-")
    assert len(pdf_bytes) > 500
    assert snapshot_hash.encode("ascii") in haystack
    assert b"approve" in haystack
    assert b"FOIR" in haystack
    assert b"BR-01" in haystack
    assert BR12_ADVISORY_BANNER.encode("ascii") in haystack
    assert b"Page 1 of" in haystack


@pytest.mark.asyncio
async def test_export_pdf_authorized_roles_and_audit(
    client: AsyncClient,
    test_user: dict[str, Any],
    reviewer_user: dict[str, Any],
    admin_user: dict[str, Any],
    db_conn: AsyncConnection[Any],
) -> None:
    seeded = await _seed_exportable_assessment(
        db_conn,
        officer_user_id=test_user["id"],
        reviewer_user_id=reviewer_user["id"],
    )
    application_id = seeded["application_id"]
    expected_filename = f'attachment; filename="loanlens_assessment_{application_id}.pdf"'

    try:
        unauth = await client.get(
            f"/api/v1/applications/{application_id}/assessment/export"
        )
        assert unauth.status_code == 401, unauth.text

        for actor in (reviewer_user, test_user, admin_user):
            csrf = await _login(
                client,
                email=actor["email"],
                password=actor["password"],
            )
            response = await client.get(
                f"/api/v1/applications/{application_id}/assessment/export"
            )
            assert response.status_code == 200, response.text
            assert response.headers["content-type"].startswith("application/pdf")
            assert response.headers["content-disposition"] == expected_filename
            body = response.content
            haystack = _pdf_searchable_bytes(body)
            assert body.startswith(b"%PDF-")
            assert len(body) > 500
            assert seeded["snapshot_hash"].encode("ascii") in haystack
            assert b"approve" in haystack
            assert b"FOIR" in haystack
            assert b"BR-01" in haystack or b"BR-05" in haystack
            assert BR12_ADVISORY_BANNER.encode("ascii") in haystack
            await client.post(
                "/api/v1/auth/logout",
                headers={"X-CSRF-Token": csrf},
            )

        audit = await db_conn.execute(
            """
            SELECT action, entity_type, assessment_id
              FROM audit_events
             WHERE application_id = %s
               AND action = 'assessment.exported'
             ORDER BY chain_seq DESC
            """,
            (application_id,),
        )
        events = await audit.fetchall()
        assert len(events) >= 3
        assert all(row["action"] == "assessment.exported" for row in events)
        assert all(row["entity_type"] == "assessment" for row in events)
        assert all(
            row["assessment_id"] == seeded["assessment_id"] for row in events
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
async def test_generate_assessment_report_is_read_only_on_domain_tables(
    db_conn: AsyncConnection[Any],
    test_user: dict[str, Any],
    reviewer_user: dict[str, Any],
) -> None:
    seeded = await _seed_exportable_assessment(
        db_conn,
        officer_user_id=test_user["id"],
        reviewer_user_id=reviewer_user["id"],
    )
    application_id = seeded["application_id"]
    try:
        before_assessment = await db_conn.execute(
            """
            SELECT recommendation::text AS recommendation, decided_at, updated_at
              FROM assessments WHERE id = %s
            """,
            (seeded["assessment_id"],),
        )
        before = await before_assessment.fetchone()
        assert before is not None

        before_calc = await db_conn.execute(
            """
            SELECT COUNT(*)::int AS cnt
              FROM application_financial_calculations
             WHERE application_id = %s
            """,
            (application_id,),
        )
        calc_count = (await before_calc.fetchone())["cnt"]

        before_findings = await db_conn.execute(
            """
            SELECT COUNT(*)::int AS cnt
              FROM verification_findings
             WHERE application_id = %s
            """,
            (application_id,),
        )
        finding_count = (await before_findings.fetchone())["cnt"]

        async with db_conn.transaction():
            pdf_bytes = await generate_assessment_report(
                db_conn,
                application_id,
                reviewer_user["id"],
                request_id="test-export-readonly",
            )

        haystack = _pdf_searchable_bytes(pdf_bytes)
        assert pdf_bytes.startswith(b"%PDF-")
        assert seeded["snapshot_hash"].encode("ascii") in haystack
        assert BR12_ADVISORY_BANNER.encode("ascii") in haystack

        after_assessment = await db_conn.execute(
            """
            SELECT recommendation::text AS recommendation, decided_at, updated_at
              FROM assessments WHERE id = %s
            """,
            (seeded["assessment_id"],),
        )
        after = await after_assessment.fetchone()
        assert after == before

        after_calc = await db_conn.execute(
            """
            SELECT COUNT(*)::int AS cnt
              FROM application_financial_calculations
             WHERE application_id = %s
            """,
            (application_id,),
        )
        assert (await after_calc.fetchone())["cnt"] == calc_count

        after_findings = await db_conn.execute(
            """
            SELECT COUNT(*)::int AS cnt
              FROM verification_findings
             WHERE application_id = %s
            """,
            (application_id,),
        )
        assert (await after_findings.fetchone())["cnt"] == finding_count

        audit = await db_conn.execute(
            """
            SELECT 1 AS present
              FROM audit_events
             WHERE application_id = %s
               AND action = 'assessment.exported'
             LIMIT 1
            """,
            (application_id,),
        )
        assert await audit.fetchone() is not None
    finally:
        await _cleanup_application(
            db_conn,
            application_id,
            policy_document_id=seeded["policy_document_id"],
            policy_version_id=seeded["policy_version_id"],
            stored_file_id=seeded["stored_file_id"],
        )
