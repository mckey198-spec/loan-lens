"""Document extraction engine and extract pipeline node (M07-T03)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from psycopg import AsyncConnection
from psycopg.errors import InsufficientPrivilege

from modules.extraction.parsers import (
    extract_bank_statement,
    extract_pan_card,
    extract_salary_slip,
)
from modules.extraction.schemas import ExtractionError
from modules.worker.nodes.extract import extract_documents
from modules.worker.schemas import RunClaim
from shared.db import create_pool
from shared.settings import get_settings
from tests.conftest import _insert_test_user, _purge_user

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

_BANK_TEXT = """
Account Statement
Account Number: 123456789012
IFSC: HDFC0001234
Statement Period: 01/01/2026 to 31/01/2026
Average Monthly Balance: 45,000.00
Month: 2026-01
Total Credits: 120000.00
Total Debits: 80000.00
Closing Balance: 45000.00
Salary Credit: 60000.00
"""

_UNPARSEABLE_TEXT = "this document has no extractable banking or identity fields"


async def _cleanup_extractions(
    db_conn: AsyncConnection[Any],
    application_id: UUID,
) -> None:
    await db_conn.execute(
        "ALTER TABLE bank_statement_monthly_summary "
        "DISABLE TRIGGER bank_statement_monthly_summary_append_only_trg"
    )
    await db_conn.execute(
        "DELETE FROM bank_statement_monthly_summary WHERE application_id = %s",
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
    await db_conn.commit()


async def _cleanup_application(
    db_conn: AsyncConnection[Any],
    application_id: UUID,
) -> None:
    await db_conn.execute(
        "ALTER TABLE document_failure_events "
        "DISABLE TRIGGER document_failure_events_append_only_trg"
    )
    await db_conn.execute(
        "DELETE FROM document_failure_events WHERE application_id = %s",
        (application_id,),
    )
    await db_conn.execute(
        "ALTER TABLE document_failure_events "
        "ENABLE TRIGGER document_failure_events_append_only_trg"
    )
    await _cleanup_extractions(db_conn, application_id)
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
    await db_conn.execute(
        "DELETE FROM processing_runs WHERE application_id = %s",
        (application_id,),
    )
    await db_conn.execute("SELECT set_config('loanlens.allow_audit_append', 'on', true)")
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


async def _insert_application(
    db_conn: AsyncConnection[Any],
    *,
    created_by_user_id: UUID,
) -> UUID:
    now = datetime.now(timezone.utc)
    ref = f"LL-EXT-{uuid4().hex[:10].upper()}"
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
    original_filename: str,
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
            %s, %s, %s, 'text/plain', 256, true, 'clamav', %s, 'daily.cvd', %s
        )
        RETURNING id
        """,
        (
            content_sha256,
            f"file:///tmp/{content_sha256[:16]}.txt",
            original_filename,
            now,
            now,
        ),
    )
    result = await row.fetchone()
    assert result is not None
    return result["id"]


async def _insert_classified_document(
    db_conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    stored_file_id: UUID,
    content_sha256: str,
    uploaded_by_user_id: UUID,
    document_type: str,
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
            classification_confidence,
            classification_method,
            uploaded_by_user_id,
            created_at,
            updated_at
        ) VALUES (
            %s,
            %s,
            %s,
            %s::document_type,
            'classified'::document_status,
            %s,
            'heuristic_rules',
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
            Decimal("0.9500"),
            uploaded_by_user_id,
            now,
            now,
        ),
    )
    result = await row.fetchone()
    assert result is not None
    return result["id"]


async def _insert_running_claim(
    db_conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    claim_fence: int = 9,
) -> RunClaim:
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
            locked_by,
            locked_at,
            lease_expires_at,
            started_at,
            created_at
        ) VALUES (
            %s,
            NULL,
            'running'::processing_run_status,
            1,
            'submit',
            %s,
            'worker-extract-test',
            %s,
            %s,
            %s,
            %s
        )
        RETURNING
            id,
            application_id,
            input_snapshot_id,
            status::text AS status,
            attempt_number,
            trigger,
            idempotency_key,
            fence_token,
            locked_by,
            locked_at,
            lease_expires_at,
            started_at,
            provider_snapshot_json,
            created_at
        """,
        (application_id, claim_fence, now, now, now, now),
    )
    result = await row.fetchone()
    assert result is not None
    await db_conn.commit()
    return RunClaim.model_validate(dict(result))


def test_extract_pan_preserves_raw_and_normalized_layers() -> None:
    result = extract_pan_card(_PAN_TEXT)
    by_name = {field.field_name: field for field in result.fields}
    assert by_name["pan"].raw_value == "ABCDE1234F"
    assert by_name["pan"].normalized_value == {"value": "ABCDE1234F"}
    assert by_name["applicant_name"].raw_value == "Test Applicant"
    assert by_name["date_of_birth"].raw_value == "15/08/1990"
    assert by_name["date_of_birth"].normalized_value == {"value": "1990-08-15"}
    assert by_name["pan"].bbox is not None
    assert by_name["pan"].page_number == 1


def test_extract_salary_slip_breakdown_and_amounts() -> None:
    result = extract_salary_slip(_SALARY_TEXT)
    by_name = {field.field_name: field for field in result.fields}
    assert by_name["employer_name"].raw_value == "Acme Corp"
    assert by_name["gross_salary"].raw_value == "65,000.00"
    assert by_name["gross_salary"].normalized_value == {
        "amount": "65000.00",
        "currency": "INR",
    }
    assert by_name["net_salary"].normalized_value == {
        "amount": "60000.00",
        "currency": "INR",
    }
    assert result.salary_slip_data is not None
    assert result.salary_slip_data.employer_name == "Acme Corp"
    assert result.salary_slip_data.pay_period_month == date(2026, 1, 1)
    assert result.salary_slip_data.basic_pay == Decimal("50000.00")
    assert result.salary_slip_data.allowances == Decimal("15000.00")
    assert result.salary_slip_data.deductions == Decimal("5000.00")
    assert result.salary_slip_data.net_pay == Decimal("60000.00")
    assert result.salary_slip_data.employee_pan == "ABCDE1234F"


def test_extract_bank_statement_metadata_and_monthly_summary() -> None:
    result = extract_bank_statement(_BANK_TEXT)
    by_name = {field.field_name: field for field in result.fields}
    assert by_name["account_number"].raw_value == "123456789012"
    assert by_name["ifsc"].normalized_value == {"value": "HDFC0001234"}
    assert by_name["statement_start_date"].normalized_value == {
        "value": "2026-01-01"
    }
    assert by_name["statement_end_date"].normalized_value == {"value": "2026-01-31"}
    assert by_name["average_monthly_balance"].normalized_value == {
        "amount": "45000.00",
        "currency": "INR",
    }
    assert result.bank_summaries is not None
    assert len(result.bank_summaries) == 1
    summary = result.bank_summaries[0]
    assert summary.statement_month == date(2026, 1, 1)
    assert summary.total_credits == Decimal("120000.00")
    assert summary.total_debits == Decimal("80000.00")
    assert summary.closing_balance == Decimal("45000.00")
    assert summary.identified_salary_credits == Decimal("60000.00")
    assert summary.salary_credit_count == 1


def test_extract_unparseable_raises() -> None:
    with pytest.raises(ExtractionError) as exc_info:
        extract_pan_card(_UNPARSEABLE_TEXT)
    assert exc_info.value.code == "extraction_failed"


@pytest.mark.asyncio
async def test_extract_node_persists_fields_summaries_bbox_and_failures(
    db_conn: AsyncConnection[Any],
) -> None:
    settings = get_settings()
    user = await _insert_test_user(
        db_conn,
        email="extract.node@example.com",
        full_name="Extract Node",
        role="loan_officer",
        password="test-password-not-a-secret",
    )
    application_id: UUID | None = None
    stored_ids: list[UUID] = []
    pool = create_pool(settings.DATABASE_URL)
    await pool.open()
    try:
        application_id = await _insert_application(
            db_conn,
            created_by_user_id=user["id"],
        )
        docs: dict[str, UUID] = {}
        fixtures = (
            ("pan", "pan_card", "pan.txt", _PAN_TEXT),
            ("salary", "salary_slip", "salary.txt", _SALARY_TEXT),
            ("bank", "bank_statement", "bank.txt", _BANK_TEXT),
            ("bad", "pan_card", "bad_pan.txt", _UNPARSEABLE_TEXT),
        )
        for key, doc_type, filename, _text in fixtures:
            content_hash = uuid4().hex + uuid4().hex
            assert len(content_hash) == 64
            stored = await _insert_stored_file(
                db_conn,
                content_sha256=content_hash,
                original_filename=filename,
            )
            stored_ids.append(stored)
            docs[key] = await _insert_classified_document(
                db_conn,
                application_id=application_id,
                stored_file_id=stored,
                content_sha256=content_hash,
                uploaded_by_user_id=user["id"],
                document_type=doc_type,
            )
        await db_conn.commit()

        claim = await _insert_running_claim(
            db_conn,
            application_id=application_id,
            claim_fence=13,
        )
        state = {
            "application_id": application_id,
            "processing_run_id": claim.id,
            "claim_fence": claim.claim_fence,
            "input_snapshot_id": None,
            "_db_pool": pool,
            "document_texts": {
                str(docs["pan"]): _PAN_TEXT,
                str(docs["salary"]): _SALARY_TEXT,
                str(docs["bank"]): _BANK_TEXT,
                str(docs["bad"]): _UNPARSEABLE_TEXT,
            },
        }
        updated = await extract_documents(state)
        assert updated["extracted_count"] == 3
        assert updated["extraction_failed_count"] == 1
        assert "_db_pool" not in updated

        statuses = await db_conn.execute(
            """
            SELECT id, status::text AS status
              FROM application_documents
             WHERE application_id = %s
            """,
            (application_id,),
        )
        status_map = {row["id"]: row["status"] for row in await statuses.fetchall()}
        assert status_map[docs["pan"]] == "extracted"
        assert status_map[docs["salary"]] == "extracted"
        assert status_map[docs["bank"]] == "extracted"
        assert status_map[docs["bad"]] == "failed"

        pan_fields = await db_conn.execute(
            """
            SELECT
                f.field_name::text AS field_name,
                f.raw_value,
                f.normalized_value_json,
                f.confidence,
                f.page_number,
                f.bbox_x_min,
                f.bbox_y_min,
                f.bbox_x_max,
                f.bbox_y_max
              FROM document_extracted_fields AS f
              INNER JOIN document_extractions AS e ON e.id = f.extraction_id
             WHERE e.application_document_id = %s
             ORDER BY f.field_name::text
            """,
            (docs["pan"],),
        )
        pan_rows = {row["field_name"]: dict(row) for row in await pan_fields.fetchall()}
        assert pan_rows["pan"]["raw_value"] == "ABCDE1234F"
        assert pan_rows["pan"]["normalized_value_json"] == {"value": "ABCDE1234F"}
        assert pan_rows["date_of_birth"]["raw_value"] == "15/08/1990"
        assert pan_rows["date_of_birth"]["normalized_value_json"] == {
            "value": "1990-08-15"
        }
        assert pan_rows["pan"]["bbox_x_min"] is not None
        assert pan_rows["pan"]["bbox_y_min"] is not None
        assert pan_rows["pan"]["bbox_x_max"] is not None
        assert pan_rows["pan"]["bbox_y_max"] is not None
        assert pan_rows["pan"]["page_number"] == 1

        salary = await db_conn.execute(
            """
            SELECT
                pay_period_month,
                basic_pay,
                allowances,
                deductions,
                net_pay,
                employer_name,
                employee_pan
              FROM salary_slip_extractions
             WHERE application_id = %s
            """,
            (application_id,),
        )
        salary_row = await salary.fetchone()
        assert salary_row is not None
        assert salary_row["pay_period_month"] == date(2026, 1, 1)
        assert salary_row["basic_pay"] == Decimal("50000.00")
        assert salary_row["allowances"] == Decimal("15000.00")
        assert salary_row["deductions"] == Decimal("5000.00")
        assert salary_row["net_pay"] == Decimal("60000.00")
        assert salary_row["employer_name"] == "Acme Corp"
        assert salary_row["employee_pan"] == "ABCDE1234F"

        bank = await db_conn.execute(
            """
            SELECT
                statement_month,
                total_credits,
                total_debits,
                closing_balance,
                identified_salary_credits,
                salary_credit_count
              FROM bank_statement_monthly_summary
             WHERE application_id = %s
            """,
            (application_id,),
        )
        bank_row = await bank.fetchone()
        assert bank_row is not None
        assert bank_row["statement_month"] == date(2026, 1, 1)
        assert bank_row["total_credits"] == Decimal("120000.00")
        assert bank_row["total_debits"] == Decimal("80000.00")
        assert bank_row["closing_balance"] == Decimal("45000.00")
        assert bank_row["identified_salary_credits"] == Decimal("60000.00")
        assert bank_row["salary_credit_count"] == 1

        failures = await db_conn.execute(
            """
            SELECT
                application_document_id,
                kind::text AS kind,
                claim_fence,
                processing_run_id,
                reason
              FROM document_failure_events
             WHERE application_id = %s
            """,
            (application_id,),
        )
        events = [dict(row) for row in await failures.fetchall()]
        assert len(events) == 1
        assert events[0]["application_document_id"] == docs["bad"]
        assert events[0]["kind"] == "extraction_failed"
        assert events[0]["claim_fence"] == claim.claim_fence
        assert events[0]["processing_run_id"] == claim.id
        assert events[0]["reason"]

        failed_extraction = await db_conn.execute(
            """
            SELECT status::text AS status, error_code
              FROM document_extractions
             WHERE application_document_id = %s
            """,
            (docs["bad"],),
        )
        failed_row = await failed_extraction.fetchone()
        assert failed_row is not None
        assert failed_row["status"] == "failed"
        assert failed_row["error_code"]

        # Append-only: extracted field rows reject UPDATE/DELETE.
        sample_field = await db_conn.execute(
            """
            SELECT id
              FROM document_extracted_fields
             WHERE application_id = %s
             LIMIT 1
            """,
            (application_id,),
        )
        field_id_row = await sample_field.fetchone()
        assert field_id_row is not None
        with pytest.raises(InsufficientPrivilege):
            await db_conn.execute(
                """
                UPDATE document_extracted_fields
                   SET raw_value = 'mutated'
                 WHERE id = %s
                """,
                (field_id_row["id"],),
            )
            await db_conn.commit()
        await db_conn.rollback()
    finally:
        await pool.close()
        await db_conn.rollback()
        if application_id is not None:
            await _cleanup_application(db_conn, application_id)
        for stored_id in stored_ids:
            await _cleanup_stored_file(db_conn, stored_id)
        await _purge_user(db_conn, user["id"])
