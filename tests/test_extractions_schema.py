"""Schema tests for document extractions and page extents (M07-T01)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from psycopg import AsyncConnection
from psycopg.errors import ForeignKeyViolation, InsufficientPrivilege
from psycopg.types.json import Jsonb

from tests.conftest import _insert_test_user, _purge_user

_HASH_A = "a" * 64
_HASH_B = "b" * 64


async def _cleanup_extractions(
    db_conn: AsyncConnection[Any],
    application_id: UUID,
) -> None:
    await db_conn.execute(
        "ALTER TABLE bank_statement_monthly_summary "
        "DISABLE TRIGGER bank_statement_monthly_summary_append_only_trg"
    )
    await db_conn.execute(
        "DELETE FROM bank_statement_monthly_summary "
        "WHERE application_id = %s",
        (application_id,),
    )
    await db_conn.execute(
        "ALTER TABLE bank_statement_monthly_summary "
        "ENABLE TRIGGER bank_statement_monthly_summary_append_only_trg"
    )

    await db_conn.execute(
        "ALTER TABLE salary_slip_extractions "
        "DISABLE TRIGGER salary_slip_extractions_append_only_trg"
    )
    await db_conn.execute(
        "DELETE FROM salary_slip_extractions WHERE application_id = %s",
        (application_id,),
    )
    await db_conn.execute(
        "ALTER TABLE salary_slip_extractions "
        "ENABLE TRIGGER salary_slip_extractions_append_only_trg"
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
        "DELETE FROM document_page_extents WHERE application_id = %s",
        (application_id,),
    )
    await db_conn.commit()


async def _cleanup_documents(
    db_conn: AsyncConnection[Any],
    application_id: UUID,
) -> None:
    await _cleanup_extractions(db_conn, application_id)
    await db_conn.execute(
        "DELETE FROM application_documents WHERE application_id = %s",
        (application_id,),
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
    await _cleanup_documents(db_conn, application_id)
    await _cleanup_processing(db_conn, application_id)
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
            %s, %s, %s, 'application/pdf', 1024, true, 'clamav', %s,
            'daily.cvd', %s
        )
        RETURNING id
        """,
        (
            content_sha256,
            f"file:///{content_sha256[:16]}",
            original_filename,
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
            %s,
            %s,
            %s,
            %s::document_type,
            'uploaded'::document_status,
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
            uploaded_by_user_id,
            now,
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
    status: str = "queued",
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
            created_at
        ) VALUES (
            %s,
            NULL,
            %s::processing_run_status,
            1,
            'submit',
            0,
            %s
        )
        RETURNING id
        """,
        (application_id, status, now),
    )
    result = await row.fetchone()
    assert result is not None
    return result["id"]


async def _insert_extraction(
    db_conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    application_document_id: UUID,
    processing_run_id: UUID,
    extractor_name: str = "fake_extractor",
    extractor_version: str = "1.0.0",
    status: str = "completed",
) -> UUID:
    now = datetime.now(timezone.utc)
    row = await db_conn.execute(
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
            %s, %s, %s, %s, %s, %s::extraction_status, '{}'::jsonb, %s, %s
        )
        RETURNING id
        """,
        (
            application_id,
            application_document_id,
            processing_run_id,
            extractor_name,
            extractor_version,
            status,
            now,
            now,
        ),
    )
    result = await row.fetchone()
    assert result is not None
    return result["id"]


@pytest.mark.asyncio
async def test_insert_page_extents_extraction_and_fields_with_bbox(
    db_conn: AsyncConnection[Any],
) -> None:
    user = await _insert_test_user(
        db_conn,
        email="extract.schema.fields@example.com",
        full_name="Extract Schema Fields",
        role="loan_officer",
        password="test-password-not-a-secret",
    )
    application_id: UUID | None = None
    stored_file_id: UUID | None = None
    try:
        application_id = await _insert_draft_application(
            db_conn,
            created_by_user_id=user["id"],
            reference_code=f"LL-EXTF-{uuid4().hex[:6].upper()}",
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
            document_type="salary_slip",
        )
        run_id = await _insert_processing_run(
            db_conn,
            application_id=application_id,
        )
        now = datetime.now(timezone.utc)

        page = await db_conn.execute(
            """
            INSERT INTO document_page_extents (
                application_id,
                application_document_id,
                page_number,
                width_pts,
                height_pts,
                created_at
            ) VALUES (
                %s, %s, 1, %s, %s, %s
            )
            RETURNING id, page_number, width_pts, height_pts
            """,
            (
                application_id,
                document_id,
                Decimal("612.00"),
                Decimal("792.00"),
                now,
            ),
        )
        page_row = await page.fetchone()
        assert page_row is not None
        assert page_row["page_number"] == 1
        assert page_row["width_pts"] == Decimal("612.00")
        assert page_row["height_pts"] == Decimal("792.00")

        extraction_id = await _insert_extraction(
            db_conn,
            application_id=application_id,
            application_document_id=document_id,
            processing_run_id=run_id,
        )

        field = await db_conn.execute(
            """
            INSERT INTO document_extracted_fields (
                application_id,
                extraction_id,
                field_name,
                raw_value,
                normalized_value_json,
                confidence,
                page_number,
                bbox_x_min,
                bbox_y_min,
                bbox_x_max,
                bbox_y_max,
                created_at
            ) VALUES (
                %s, %s, 'net_salary'::extraction_field_name, '75000.00',
                %s, %s, 1, %s, %s, %s, %s, %s
            )
            RETURNING id, field_name::text AS field_name, raw_value,
                      confidence, page_number,
                      bbox_x_min, bbox_y_min, bbox_x_max, bbox_y_max
            """,
            (
                application_id,
                extraction_id,
                Jsonb({"amount": "75000.00", "currency": "INR"}),
                Decimal("0.9400"),
                Decimal("72.5000"),
                Decimal("640.0000"),
                Decimal("180.2500"),
                Decimal("660.0000"),
                now,
            ),
        )
        field_row = await field.fetchone()
        assert field_row is not None
        assert field_row["field_name"] == "net_salary"
        assert field_row["raw_value"] == "75000.00"
        assert field_row["confidence"] == Decimal("0.9400")
        assert field_row["page_number"] == 1
        assert field_row["bbox_x_min"] == Decimal("72.5000")
        assert field_row["bbox_y_min"] == Decimal("640.0000")
        assert field_row["bbox_x_max"] == Decimal("180.2500")
        assert field_row["bbox_y_max"] == Decimal("660.0000")
        await db_conn.commit()
    finally:
        await db_conn.rollback()
        if application_id is not None:
            await _cleanup_application(db_conn, application_id)
        if stored_file_id is not None:
            await _cleanup_stored_file(db_conn, stored_file_id)
        await _purge_user(db_conn, user["id"])


@pytest.mark.asyncio
async def test_insert_salary_slip_and_bank_monthly_summaries(
    db_conn: AsyncConnection[Any],
) -> None:
    user = await _insert_test_user(
        db_conn,
        email="extract.schema.struct@example.com",
        full_name="Extract Schema Struct",
        role="loan_officer",
        password="test-password-not-a-secret",
    )
    application_id: UUID | None = None
    stored_salary: UUID | None = None
    stored_bank: UUID | None = None
    try:
        application_id = await _insert_draft_application(
            db_conn,
            created_by_user_id=user["id"],
            reference_code=f"LL-EXTS-{uuid4().hex[:6].upper()}",
        )
        stored_salary = await _insert_stored_file(
            db_conn,
            content_sha256=_HASH_A,
            original_filename="salary.pdf",
        )
        stored_bank = await _insert_stored_file(
            db_conn,
            content_sha256=_HASH_B,
            original_filename="bank.pdf",
        )
        salary_doc = await _insert_application_document(
            db_conn,
            application_id=application_id,
            stored_file_id=stored_salary,
            content_sha256=_HASH_A,
            uploaded_by_user_id=user["id"],
            document_type="salary_slip",
        )
        bank_doc = await _insert_application_document(
            db_conn,
            application_id=application_id,
            stored_file_id=stored_bank,
            content_sha256=_HASH_B,
            uploaded_by_user_id=user["id"],
            document_type="bank_statement",
        )
        run_id = await _insert_processing_run(
            db_conn,
            application_id=application_id,
        )
        salary_extraction = await _insert_extraction(
            db_conn,
            application_id=application_id,
            application_document_id=salary_doc,
            processing_run_id=run_id,
            extractor_name="salary_parser",
        )
        bank_extraction = await _insert_extraction(
            db_conn,
            application_id=application_id,
            application_document_id=bank_doc,
            processing_run_id=run_id,
            extractor_name="bank_parser",
        )
        now = datetime.now(timezone.utc)

        salary = await db_conn.execute(
            """
            INSERT INTO salary_slip_extractions (
                application_id,
                extraction_id,
                pay_period_month,
                basic_pay,
                allowances,
                deductions,
                net_pay,
                employer_name,
                employee_pan,
                created_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            RETURNING id, net_pay, employer_name, employee_pan
            """,
            (
                application_id,
                salary_extraction,
                date(2026, 1, 1),
                Decimal("50000.00"),
                Decimal("15000.00"),
                Decimal("5000.00"),
                Decimal("60000.00"),
                "Acme Corp",
                "ABCDE1234F",
                now,
            ),
        )
        salary_row = await salary.fetchone()
        assert salary_row is not None
        assert salary_row["net_pay"] == Decimal("60000.00")
        assert salary_row["employer_name"] == "Acme Corp"
        assert salary_row["employee_pan"] == "ABCDE1234F"

        bank = await db_conn.execute(
            """
            INSERT INTO bank_statement_monthly_summary (
                application_id,
                extraction_id,
                statement_month,
                total_credits,
                total_debits,
                closing_balance,
                identified_salary_credits,
                salary_credit_count,
                created_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            RETURNING id, total_credits, total_debits, closing_balance,
                      identified_salary_credits, salary_credit_count
            """,
            (
                application_id,
                bank_extraction,
                date(2026, 1, 1),
                Decimal("120000.00"),
                Decimal("80000.00"),
                Decimal("45000.00"),
                Decimal("60000.00"),
                1,
                now,
            ),
        )
        bank_row = await bank.fetchone()
        assert bank_row is not None
        assert bank_row["total_credits"] == Decimal("120000.00")
        assert bank_row["total_debits"] == Decimal("80000.00")
        assert bank_row["closing_balance"] == Decimal("45000.00")
        assert bank_row["identified_salary_credits"] == Decimal("60000.00")
        assert bank_row["salary_credit_count"] == 1
        await db_conn.commit()
    finally:
        await db_conn.rollback()
        if application_id is not None:
            await _cleanup_application(db_conn, application_id)
        if stored_salary is not None:
            await _cleanup_stored_file(db_conn, stored_salary)
        if stored_bank is not None:
            await _cleanup_stored_file(db_conn, stored_bank)
        await _purge_user(db_conn, user["id"])


@pytest.mark.asyncio
async def test_composite_fks_enforce_application_isolation(
    db_conn: AsyncConnection[Any],
) -> None:
    user = await _insert_test_user(
        db_conn,
        email="extract.schema.iso@example.com",
        full_name="Extract Schema Iso",
        role="loan_officer",
        password="test-password-not-a-secret",
    )
    application_a: UUID | None = None
    application_b: UUID | None = None
    stored_a: UUID | None = None
    stored_b: UUID | None = None
    try:
        application_a = await _insert_draft_application(
            db_conn,
            created_by_user_id=user["id"],
            reference_code=f"LL-EXIA-{uuid4().hex[:6].upper()}",
        )
        application_b = await _insert_draft_application(
            db_conn,
            created_by_user_id=user["id"],
            reference_code=f"LL-EXIB-{uuid4().hex[:6].upper()}",
        )
        stored_a = await _insert_stored_file(
            db_conn,
            content_sha256=_HASH_A,
        )
        stored_b = await _insert_stored_file(
            db_conn,
            content_sha256=_HASH_B,
        )
        doc_a = await _insert_application_document(
            db_conn,
            application_id=application_a,
            stored_file_id=stored_a,
            content_sha256=_HASH_A,
            uploaded_by_user_id=user["id"],
        )
        doc_b = await _insert_application_document(
            db_conn,
            application_id=application_b,
            stored_file_id=stored_b,
            content_sha256=_HASH_B,
            uploaded_by_user_id=user["id"],
        )
        run_a = await _insert_processing_run(
            db_conn,
            application_id=application_a,
        )
        run_b = await _insert_processing_run(
            db_conn,
            application_id=application_b,
        )
        await db_conn.commit()
        now = datetime.now(timezone.utc)

        # Extraction for app A cannot reference document belonging to app B.
        with pytest.raises(ForeignKeyViolation):
            await db_conn.execute(
                """
                INSERT INTO document_extractions (
                    application_id,
                    application_document_id,
                    processing_run_id,
                    extractor_name,
                    extractor_version,
                    status,
                    created_at,
                    updated_at
                ) VALUES (
                    %s, %s, %s, 'fake', '1.0.0',
                    'pending'::extraction_status, %s, %s
                )
                """,
                (application_a, doc_b, run_a, now, now),
            )
            await db_conn.commit()
        await db_conn.rollback()

        # Extraction for app A cannot reference processing run of app B.
        with pytest.raises(ForeignKeyViolation):
            await db_conn.execute(
                """
                INSERT INTO document_extractions (
                    application_id,
                    application_document_id,
                    processing_run_id,
                    extractor_name,
                    extractor_version,
                    status,
                    created_at,
                    updated_at
                ) VALUES (
                    %s, %s, %s, 'fake', '1.0.0',
                    'pending'::extraction_status, %s, %s
                )
                """,
                (application_a, doc_a, run_b, now, now),
            )
            await db_conn.commit()
        await db_conn.rollback()

        # Page extent cannot bind app A id to app B document.
        with pytest.raises(ForeignKeyViolation):
            await db_conn.execute(
                """
                INSERT INTO document_page_extents (
                    application_id,
                    application_document_id,
                    page_number,
                    width_pts,
                    height_pts,
                    created_at
                ) VALUES (
                    %s, %s, 1, %s, %s, %s
                )
                """,
                (
                    application_a,
                    doc_b,
                    Decimal("612.00"),
                    Decimal("792.00"),
                    now,
                ),
            )
            await db_conn.commit()
        await db_conn.rollback()

        extraction_a = await _insert_extraction(
            db_conn,
            application_id=application_a,
            application_document_id=doc_a,
            processing_run_id=run_a,
        )
        await db_conn.commit()

        # Extracted field cannot attach app B to extraction of app A.
        with pytest.raises(ForeignKeyViolation):
            await db_conn.execute(
                """
                INSERT INTO document_extracted_fields (
                    application_id,
                    extraction_id,
                    field_name,
                    raw_value,
                    created_at
                ) VALUES (
                    %s, %s, 'pan'::extraction_field_name, 'ABCDE1234F', %s
                )
                """,
                (application_b, extraction_a, now),
            )
            await db_conn.commit()
        await db_conn.rollback()
    finally:
        await db_conn.rollback()
        if application_a is not None:
            await _cleanup_application(db_conn, application_a)
        if application_b is not None:
            await _cleanup_application(db_conn, application_b)
        if stored_a is not None:
            await _cleanup_stored_file(db_conn, stored_a)
        if stored_b is not None:
            await _cleanup_stored_file(db_conn, stored_b)
        await _purge_user(db_conn, user["id"])


@pytest.mark.asyncio
async def test_extracted_rows_reject_update_and_delete(
    db_conn: AsyncConnection[Any],
) -> None:
    user = await _insert_test_user(
        db_conn,
        email="extract.schema.immut@example.com",
        full_name="Extract Schema Immut",
        role="loan_officer",
        password="test-password-not-a-secret",
    )
    application_id: UUID | None = None
    stored_file_id: UUID | None = None
    try:
        application_id = await _insert_draft_application(
            db_conn,
            created_by_user_id=user["id"],
            reference_code=f"LL-EXTI-{uuid4().hex[:6].upper()}",
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
            document_type="salary_slip",
        )
        run_id = await _insert_processing_run(
            db_conn,
            application_id=application_id,
        )
        extraction_id = await _insert_extraction(
            db_conn,
            application_id=application_id,
            application_document_id=document_id,
            processing_run_id=run_id,
        )
        now = datetime.now(timezone.utc)

        field = await db_conn.execute(
            """
            INSERT INTO document_extracted_fields (
                application_id,
                extraction_id,
                field_name,
                raw_value,
                created_at
            ) VALUES (
                %s, %s, 'employer_name'::extraction_field_name,
                'Acme Corp', %s
            )
            RETURNING id
            """,
            (application_id, extraction_id, now),
        )
        field_row = await field.fetchone()
        assert field_row is not None

        salary = await db_conn.execute(
            """
            INSERT INTO salary_slip_extractions (
                application_id,
                extraction_id,
                pay_period_month,
                net_pay,
                employer_name,
                created_at
            ) VALUES (
                %s, %s, %s, %s, 'Acme Corp', %s
            )
            RETURNING id
            """,
            (
                application_id,
                extraction_id,
                date(2026, 2, 1),
                Decimal("55000.00"),
                now,
            ),
        )
        salary_row = await salary.fetchone()
        assert salary_row is not None

        bank = await db_conn.execute(
            """
            INSERT INTO bank_statement_monthly_summary (
                application_id,
                extraction_id,
                statement_month,
                total_credits,
                total_debits,
                closing_balance,
                created_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s
            )
            RETURNING id
            """,
            (
                application_id,
                extraction_id,
                date(2026, 2, 1),
                Decimal("10000.00"),
                Decimal("4000.00"),
                Decimal("20000.00"),
                now,
            ),
        )
        bank_row = await bank.fetchone()
        assert bank_row is not None
        await db_conn.commit()

        with pytest.raises(InsufficientPrivilege):
            await db_conn.execute(
                """
                UPDATE document_extracted_fields
                   SET raw_value = 'mutated'
                 WHERE id = %s
                """,
                (field_row["id"],),
            )
            await db_conn.commit()
        await db_conn.rollback()

        with pytest.raises(InsufficientPrivilege):
            await db_conn.execute(
                "DELETE FROM document_extracted_fields WHERE id = %s",
                (field_row["id"],),
            )
            await db_conn.commit()
        await db_conn.rollback()

        with pytest.raises(InsufficientPrivilege):
            await db_conn.execute(
                """
                UPDATE salary_slip_extractions
                   SET net_pay = %s
                 WHERE id = %s
                """,
                (Decimal("1.00"), salary_row["id"]),
            )
            await db_conn.commit()
        await db_conn.rollback()

        with pytest.raises(InsufficientPrivilege):
            await db_conn.execute(
                "DELETE FROM salary_slip_extractions WHERE id = %s",
                (salary_row["id"],),
            )
            await db_conn.commit()
        await db_conn.rollback()

        with pytest.raises(InsufficientPrivilege):
            await db_conn.execute(
                """
                UPDATE bank_statement_monthly_summary
                   SET total_credits = %s
                 WHERE id = %s
                """,
                (Decimal("1.00"), bank_row["id"]),
            )
            await db_conn.commit()
        await db_conn.rollback()

        with pytest.raises(InsufficientPrivilege):
            await db_conn.execute(
                "DELETE FROM bank_statement_monthly_summary WHERE id = %s",
                (bank_row["id"],),
            )
            await db_conn.commit()
        await db_conn.rollback()

        still = await db_conn.execute(
            """
            SELECT
                (SELECT raw_value FROM document_extracted_fields
                  WHERE id = %s) AS field_value,
                (SELECT net_pay FROM salary_slip_extractions
                  WHERE id = %s) AS salary_net,
                (SELECT total_credits FROM bank_statement_monthly_summary
                  WHERE id = %s) AS bank_credits
            """,
            (field_row["id"], salary_row["id"], bank_row["id"]),
        )
        retained = await still.fetchone()
        assert retained is not None
        assert retained["field_value"] == "Acme Corp"
        assert retained["salary_net"] == Decimal("55000.00")
        assert retained["bank_credits"] == Decimal("10000.00")
    finally:
        await db_conn.rollback()
        if application_id is not None:
            await _cleanup_application(db_conn, application_id)
        if stored_file_id is not None:
            await _cleanup_stored_file(db_conn, stored_file_id)
        await _purge_user(db_conn, user["id"])
