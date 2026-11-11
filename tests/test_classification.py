"""Document classification engine and classify pipeline node (M07-T02)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from psycopg import AsyncConnection

from modules.classification.classifier import classify_document_text
from modules.worker.nodes.classify import classify_documents
from modules.worker.schemas import RunClaim
from shared.db import create_pool
from shared.settings import get_settings
from tests.conftest import _insert_test_user, _purge_user

_PAN_TEXT = """
INCOME TAX DEPARTMENT
GOVT OF INDIA
Permanent Account Number Card
ABCDE1234F
Name: TEST USER
"""

_SALARY_TEXT = """
Payslip for March 2026
Employee: Test User
Earnings
  Basic Pay: 50000
Deductions
  PF: 1800
Net Pay: 48200
Salary Slip
"""

_BANK_TEXT = """
Account Statement
Opening Balance: 10000.00
Transaction Date | Description | Withdrawal | Deposit
2026-01-02 | ATM | 500.00 |
2026-01-05 | Salary Credit | | 50000.00
Closing Balance: 59500.00
"""

_GARBAGE_TEXT = "lorem ipsum dolor sit amet xyz 12345 unrelated noise"


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


async def _insert_application(
    db_conn: AsyncConnection[Any],
    *,
    created_by_user_id: UUID,
    status: str = "draft",
) -> UUID:
    now = datetime.now(timezone.utc)
    ref = f"LL-CLF-{uuid4().hex[:10].upper()}"
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
            submitted_at,
            created_at,
            updated_at
        ) VALUES (
            %s,
            %s::application_status,
            %s,
            'salaried'::applicant_type,
            %s,
            'INR',
            36,
            'home_renovation',
            %s,
            %s,
            %s
        )
        RETURNING id
        """,
        (
            ref,
            status,
            created_by_user_id,
            Decimal("500000.00"),
            now if status != "draft" else None,
            now,
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
            %s, %s, %s, 'text/plain', 128, true, 'clamav', %s, 'daily.cvd', %s
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


async def _insert_application_document(
    db_conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    stored_file_id: UUID,
    content_sha256: str,
    uploaded_by_user_id: UUID,
    document_type: str = "other",
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


async def _insert_running_claim(
    db_conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    claim_fence: int = 7,
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
            'worker-classify-test',
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
        (
            application_id,
            claim_fence,
            now,
            now,
            now,
            now,
        ),
    )
    result = await row.fetchone()
    assert result is not None
    await db_conn.commit()
    return RunClaim.model_validate(dict(result))


def test_classify_pan_card_high_confidence() -> None:
    result = classify_document_text(_PAN_TEXT, filename="pan_card.txt")
    assert result.document_type == "pan_card"
    assert result.confidence >= Decimal("0.90")
    assert result.method == "heuristic_rules"
    assert result.evidence_snippets


def test_classify_salary_slip_high_confidence() -> None:
    result = classify_document_text(_SALARY_TEXT, filename="payslip.txt")
    assert result.document_type == "salary_slip"
    assert result.confidence >= Decimal("0.90")
    assert result.method == "heuristic_rules"


def test_classify_bank_statement_high_confidence() -> None:
    result = classify_document_text(_BANK_TEXT, filename="statement.txt")
    assert result.document_type == "bank_statement"
    assert result.confidence >= Decimal("0.90")
    assert result.method == "heuristic_rules"


def test_classify_garbage_returns_unsupported() -> None:
    result = classify_document_text(_GARBAGE_TEXT, filename="mystery.bin")
    assert result.document_type == "unsupported"
    assert result.confidence == Decimal("0.0")


def test_classify_empty_text_returns_unsupported() -> None:
    result = classify_document_text("   ", filename="empty.txt")
    assert result.document_type == "unsupported"
    assert result.confidence == Decimal("0.0")
    assert result.method == "heuristic_rules"


@pytest.mark.asyncio
async def test_classify_node_updates_documents_and_failure_events(
    db_conn: AsyncConnection[Any],
) -> None:
    settings = get_settings()
    user = await _insert_test_user(
        db_conn,
        email="classify.node@example.com",
        full_name="Classify Node",
        role="loan_officer",
        password="test-password-not-a-secret",
    )
    application_id: UUID | None = None
    pool = create_pool(settings.DATABASE_URL)
    await pool.open()
    try:
        application_id = await _insert_application(
            db_conn,
            created_by_user_id=user["id"],
            status="draft",
        )
        pan_hash = uuid4().hex + uuid4().hex  # 64 hex chars
        junk_hash = uuid4().hex + uuid4().hex
        assert len(pan_hash) == 64 and len(junk_hash) == 64
        pan_file = await _insert_stored_file(
            db_conn,
            content_sha256=pan_hash,
            original_filename="pan.txt",
        )
        junk_file = await _insert_stored_file(
            db_conn,
            content_sha256=junk_hash,
            original_filename="noise.txt",
        )
        pan_doc = await _insert_application_document(
            db_conn,
            application_id=application_id,
            stored_file_id=pan_file,
            content_sha256=pan_hash,
            uploaded_by_user_id=user["id"],
            document_type="other",
        )
        junk_doc = await _insert_application_document(
            db_conn,
            application_id=application_id,
            stored_file_id=junk_file,
            content_sha256=junk_hash,
            uploaded_by_user_id=user["id"],
            document_type="other",
        )
        now = datetime.now(timezone.utc)
        await db_conn.execute(
            """
            INSERT INTO application_state_history (
                application_id,
                from_status,
                to_status,
                actor_type,
                actor_user_id,
                reason,
                created_at
            ) VALUES (
                %s,
                'draft'::application_status,
                'submitted'::application_status,
                'user'::audit_actor_type,
                %s,
                'test_submit',
                %s
            )
            """,
            (application_id, user["id"], now),
        )
        await db_conn.execute(
            """
            UPDATE applications
               SET status = 'submitted'::application_status,
                   submitted_at = %s,
                   updated_at = %s
             WHERE id = %s
            """,
            (now, now, application_id),
        )
        await db_conn.execute(
            """
            INSERT INTO application_state_history (
                application_id,
                from_status,
                to_status,
                actor_type,
                actor_user_id,
                reason,
                created_at
            ) VALUES (
                %s,
                'submitted'::application_status,
                'processing'::application_status,
                'system'::audit_actor_type,
                NULL,
                'test_processing',
                %s
            )
            """,
            (application_id, now),
        )
        await db_conn.execute(
            """
            UPDATE applications
               SET status = 'processing'::application_status,
                   updated_at = %s
             WHERE id = %s
            """,
            (now, application_id),
        )
        await db_conn.commit()

        claim = await _insert_running_claim(
            db_conn,
            application_id=application_id,
            claim_fence=11,
        )

        state = {
            "application_id": application_id,
            "processing_run_id": claim.id,
            "claim_fence": claim.claim_fence,
            "input_snapshot_id": None,
            "_db_pool": pool,
            "document_texts": {
                str(pan_doc): _PAN_TEXT,
                str(junk_doc): _GARBAGE_TEXT,
            },
        }
        updated = await classify_documents(state)
        assert updated["classified_count"] == 2
        assert updated["unsupported_count"] == 1
        assert "_db_pool" not in updated

        docs = await db_conn.execute(
            """
            SELECT
                id,
                document_type::text AS document_type,
                status::text AS status,
                classification_confidence,
                classification_method
              FROM application_documents
             WHERE application_id = %s
             ORDER BY created_at ASC, id ASC
            """,
            (application_id,),
        )
        rows = {row["id"]: dict(row) for row in await docs.fetchall()}

        assert rows[pan_doc]["document_type"] == "pan_card"
        assert rows[pan_doc]["status"] == "classified"
        assert rows[pan_doc]["classification_confidence"] >= Decimal("0.90")
        assert rows[pan_doc]["classification_method"] == "heuristic_rules"

        assert rows[junk_doc]["document_type"] == "unsupported"
        assert rows[junk_doc]["status"] == "unsupported"
        assert rows[junk_doc]["classification_confidence"] == Decimal("0.0")

        failures = await db_conn.execute(
            """
            SELECT
                application_document_id,
                kind::text AS kind,
                claim_fence,
                reason,
                processing_run_id
              FROM document_failure_events
             WHERE application_id = %s
            """,
            (application_id,),
        )
        events = [dict(row) for row in await failures.fetchall()]
        assert len(events) == 1
        event = events[0]
        assert event["application_document_id"] == junk_doc
        assert event["kind"] == "marked_unsupported"
        assert event["claim_fence"] == claim.claim_fence
        assert event["processing_run_id"] == claim.id
        assert event["reason"]
    finally:
        await pool.close()
        try:
            await db_conn.rollback()
        except Exception:  # noqa: BLE001 — best-effort before cleanup
            pass
        if application_id is not None:
            await _cleanup_application(db_conn, application_id)
        # stored_files are insert-only; leave orphan rows keyed by unique hashes.
        await _purge_user(db_conn, user["id"])
