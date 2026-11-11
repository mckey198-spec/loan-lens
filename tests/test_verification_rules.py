"""Deterministic verification rules BR-01…BR-11 and verify pipeline node (M08)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from psycopg import AsyncConnection
from psycopg.errors import InsufficientPrivilege
from psycopg.types.json import Jsonb

from modules.verification.engine import evaluate_all_rules
from modules.verification.rules import (
    br01_name,
    br02_pan,
    br03_dob,
    br04_employer,
    br05_salary_credit,
    br06_income_discrepancy,
    br07_tenure,
    br08_min_income,
    br09_statement_continuity,
    br10_emi_corroboration,
    br11_doc_completeness,
)
from modules.verification.rules._text import token_jaccard
from modules.verification.schemas import (
    LayeredField,
    MandatoryDocumentRequirement,
    PolicyThresholds,
    RecurringDebitObservation,
    RuleEvaluationContext,
    RuleTolerances,
    SalaryCreditObservation,
    SnapshotFacts,
    StatementPeriod,
)
from modules.verification.service import build_context_from_parts, persist_verification_results
from modules.worker.nodes.verify import verify_rules
from shared.db import create_pool
from shared.settings import get_settings
from tests.conftest import _insert_test_user, _purge_user

_HASH_A = "a" * 64


def _thresholds(
    *,
    min_employment_months: int = 12,
    min_monthly_income: str = "25000.00",
) -> PolicyThresholds:
    return PolicyThresholds(
        min_employment_months=min_employment_months,
        min_monthly_income=Decimal(min_monthly_income),
        max_foir_pct=Decimal("50.00"),
        max_loan_amount=Decimal("2500000.00"),
    )


def _snapshot(**overrides: Any) -> SnapshotFacts:
    base: dict[str, Any] = {
        "applicant_full_name": "Test Applicant",
        "date_of_birth": date(1990, 8, 15),
        "pan": "ABCDE1234F",
        "employer_or_business_name": "Acme Corp",
        "time_in_job_or_business_months": 24,
        "declared_gross_monthly_income": Decimal("65000.00"),
        "declared_net_monthly_income": Decimal("60000.00"),
        "declared_existing_emis": Decimal("0.00"),
        "applicant_type": "salaried",
        "as_of_date": date(2026, 1, 15),
        "document_inventory": [
            {"document_type": "pan_card"},
            {"document_type": "salary_slip"},
            {"document_type": "bank_statement"},
        ],
    }
    base.update(overrides)
    return SnapshotFacts(**base)


def _context(**overrides: Any) -> RuleEvaluationContext:
    base: dict[str, Any] = {
        "snapshot": _snapshot(),
        "fields": [],
        "salary_slip_net_pays": [],
        "salary_credits": [],
        "statement_periods": [],
        "recurring_debits": [],
        "mandatory_documents": [
            MandatoryDocumentRequirement(document_type="pan_card"),
            MandatoryDocumentRequirement(document_type="salary_slip"),
            MandatoryDocumentRequirement(document_type="bank_statement"),
        ],
        "policy_thresholds": _thresholds(),
        "tolerances": RuleTolerances(),
    }
    base.update(overrides)
    return RuleEvaluationContext(**base)


def test_token_jaccard_name_threshold() -> None:
    assert token_jaccard("Test Applicant", "Test Applicant") == Decimal("1.0000")
    assert token_jaccard("Test Applicant", "Applicant Test") >= Decimal("0.85")
    assert token_jaccard("Test Applicant", "Completely Different") < Decimal("0.85")


def test_br01_name_pass_and_fail() -> None:
    passed = br01_name.evaluate(
        _context(
            fields=[
                LayeredField(
                    field_name="applicant_name",
                    document_type="pan_card",
                    raw_value="TEST APPLICANT",
                    normalized_value={"value": "Test Applicant"},
                )
            ]
        )
    )
    assert passed.status == "passed"

    failed = br01_name.evaluate(
        _context(
            fields=[
                LayeredField(
                    field_name="applicant_name",
                    document_type="pan_card",
                    normalized_value={"value": "Totally Other Person"},
                )
            ]
        )
    )
    assert failed.status == "failed"
    assert failed.severity == "high"


def test_br02_pan_pass_and_fail() -> None:
    passed = br02_pan.evaluate(
        _context(
            fields=[
                LayeredField(
                    field_name="pan",
                    document_type="pan_card",
                    raw_value="abcde1234f",
                    normalized_value={"value": "ABCDE1234F"},
                )
            ]
        )
    )
    assert passed.status == "passed"

    failed = br02_pan.evaluate(
        _context(
            fields=[
                LayeredField(
                    field_name="pan",
                    document_type="pan_card",
                    normalized_value={"value": "ZZZZZ9999Z"},
                )
            ]
        )
    )
    assert failed.status == "failed"
    assert failed.severity == "critical"


def test_br03_dob_pass_and_fail() -> None:
    passed = br03_dob.evaluate(
        _context(
            fields=[
                LayeredField(
                    field_name="date_of_birth",
                    document_type="pan_card",
                    normalized_value={"value": "1990-08-15"},
                )
            ]
        )
    )
    assert passed.status == "passed"

    failed = br03_dob.evaluate(
        _context(
            fields=[
                LayeredField(
                    field_name="date_of_birth",
                    document_type="pan_card",
                    normalized_value={"value": "1991-01-01"},
                )
            ]
        )
    )
    assert failed.status == "failed"
    assert failed.severity == "critical"


def test_br04_employer_pass_and_fail() -> None:
    passed = br04_employer.evaluate(
        _context(
            fields=[
                LayeredField(
                    field_name="employer_name",
                    document_type="salary_slip",
                    normalized_value={"value": "Acme Corp"},
                )
            ]
        )
    )
    assert passed.status == "passed"

    failed = br04_employer.evaluate(
        _context(
            fields=[
                LayeredField(
                    field_name="employer_name",
                    document_type="salary_slip",
                    normalized_value={"value": "Other Employer Ltd"},
                )
            ]
        )
    )
    assert failed.status == "failed"


def test_br05_salary_credit_pass_and_fail() -> None:
    passed = br05_salary_credit.evaluate(
        _context(
            salary_slip_net_pays=[Decimal("60000.00")],
            salary_credits=[
                SalaryCreditObservation(
                    statement_month=date(2026, 1, 1),
                    amount=Decimal("60500.00"),
                )
            ],
        )
    )
    assert passed.status == "passed"

    failed = br05_salary_credit.evaluate(
        _context(
            salary_slip_net_pays=[Decimal("60000.00")],
            salary_credits=[
                SalaryCreditObservation(
                    statement_month=date(2026, 1, 1),
                    amount=Decimal("40000.00"),
                )
            ],
        )
    )
    assert failed.status == "failed"


def test_br06_income_discrepancy_pass_and_fail() -> None:
    passed = br06_income_discrepancy.evaluate(
        _context(
            salary_slip_net_pays=[Decimal("61000.00")],
        )
    )
    assert passed.status == "passed"

    failed = br06_income_discrepancy.evaluate(
        _context(
            salary_slip_net_pays=[Decimal("40000.00")],
        )
    )
    assert failed.status == "failed"


def test_br07_tenure_pass_and_fail() -> None:
    passed = br07_tenure.evaluate(_context())
    assert passed.status == "passed"

    failed = br07_tenure.evaluate(
        _context(
            snapshot=_snapshot(time_in_job_or_business_months=3),
            policy_thresholds=_thresholds(min_employment_months=12),
        )
    )
    assert failed.status == "failed"


def test_br08_min_income_pass_and_fail() -> None:
    passed = br08_min_income.evaluate(
        _context(salary_slip_net_pays=[Decimal("60000.00")])
    )
    assert passed.status == "passed"

    failed = br08_min_income.evaluate(
        _context(
            snapshot=_snapshot(declared_net_monthly_income=Decimal("10000.00")),
            salary_slip_net_pays=[],
            policy_thresholds=_thresholds(min_monthly_income="25000.00"),
        )
    )
    assert failed.status == "failed"


def test_br09_statement_continuity_pass_and_fail() -> None:
    passed = br09_statement_continuity.evaluate(
        _context(
            statement_periods=[
                StatementPeriod(
                    start_date=date(2025, 10, 1),
                    end_date=date(2025, 12, 31),
                ),
                StatementPeriod(
                    start_date=date(2026, 1, 1),
                    end_date=date(2026, 1, 31),
                ),
            ]
        )
    )
    assert passed.status == "passed"

    failed = br09_statement_continuity.evaluate(
        _context(
            statement_periods=[
                StatementPeriod(
                    start_date=date(2025, 10, 1),
                    end_date=date(2025, 10, 31),
                ),
                StatementPeriod(
                    start_date=date(2026, 1, 1),
                    end_date=date(2026, 1, 31),
                ),
            ]
        )
    )
    assert failed.status == "failed"
    assert failed.diff_payload["gaps"]


def test_br10_emi_corroboration_pass_and_fail() -> None:
    passed = br10_emi_corroboration.evaluate(
        _context(
            snapshot=_snapshot(declared_existing_emis=Decimal("10000.00")),
            recurring_debits=[
                RecurringDebitObservation(
                    label="HOME LOAN EMI",
                    monthly_amount=Decimal("10000.00"),
                    occurrence_count=3,
                )
            ],
        )
    )
    assert passed.status == "passed"

    failed = br10_emi_corroboration.evaluate(
        _context(
            snapshot=_snapshot(declared_existing_emis=Decimal("0.00")),
            recurring_debits=[
                RecurringDebitObservation(
                    label="PERSONAL LOAN EMI",
                    monthly_amount=Decimal("8000.00"),
                    occurrence_count=3,
                )
            ],
        )
    )
    assert failed.status == "failed"


def test_br11_doc_completeness_pass_and_fail() -> None:
    passed = br11_doc_completeness.evaluate(_context())
    assert passed.status == "passed"

    failed = br11_doc_completeness.evaluate(
        _context(
            snapshot=_snapshot(
                document_inventory=[{"document_type": "pan_card"}]
            )
        )
    )
    assert failed.status == "failed"
    assert failed.severity == "critical"


def test_evaluate_all_rules_returns_eleven_pure_results() -> None:
    results = evaluate_all_rules(
        _context(
            fields=[
                LayeredField(
                    field_name="applicant_name",
                    normalized_value={"value": "Test Applicant"},
                ),
                LayeredField(
                    field_name="pan",
                    normalized_value={"value": "ABCDE1234F"},
                ),
                LayeredField(
                    field_name="date_of_birth",
                    normalized_value={"value": "1990-08-15"},
                ),
                LayeredField(
                    field_name="employer_name",
                    normalized_value={"value": "Acme Corp"},
                ),
                LayeredField(
                    field_name="net_salary",
                    normalized_value={"amount": "60000.00"},
                ),
            ],
            salary_slip_net_pays=[Decimal("60000.00")],
            salary_credits=[
                SalaryCreditObservation(
                    statement_month=date(2026, 1, 1),
                    amount=Decimal("60000.00"),
                )
            ],
            statement_periods=[
                StatementPeriod(
                    start_date=date(2026, 1, 1),
                    end_date=date(2026, 1, 31),
                )
            ],
        )
    )
    assert len(results) == 11
    assert [item.rule_code for item in results] == [
        "BR-01",
        "BR-02",
        "BR-03",
        "BR-04",
        "BR-05",
        "BR-06",
        "BR-07",
        "BR-08",
        "BR-09",
        "BR-10",
        "BR-11",
    ]
    assert all(item.status in {"passed", "flagged", "failed"} for item in results)


def test_corrections_override_raw_and_normalized_layers() -> None:
    """Layer 4 corrected_value wins over Layer 2 normalized and Layer 1 raw."""
    context = _context(
        fields=[
            LayeredField(
                field_name="pan",
                document_type="pan_card",
                raw_value="WRONG1111W",
                normalized_value={"value": "WRONG1111W"},
                corrected_value={"value": "ABCDE1234F"},
            ),
            LayeredField(
                field_name="applicant_name",
                document_type="pan_card",
                raw_value="Wrong Name",
                normalized_value={"value": "Wrong Name"},
                corrected_value={"value": "Test Applicant"},
            ),
            LayeredField(
                field_name="date_of_birth",
                document_type="pan_card",
                raw_value="01/01/2000",
                normalized_value={"value": "2000-01-01"},
                corrected_value={"value": "1990-08-15"},
            ),
        ]
    )
    pan = br02_pan.evaluate(context)
    name = br01_name.evaluate(context)
    dob = br03_dob.evaluate(context)
    assert pan.status == "passed"
    assert name.status == "passed"
    assert dob.status == "passed"
    assert pan.provenance_sources[-1]["layer"] == "corrected"
    assert name.provenance_sources[-1]["layer"] == "corrected"
    assert dob.provenance_sources[-1]["layer"] == "corrected"


def test_engine_has_no_llm_imports() -> None:
    import modules.verification.engine as engine_mod
    import modules.verification.rules as rules_mod

    forbidden = ("openai", "anthropic", "langchain", "langgraph", "httpx", "requests")
    for module in (engine_mod, rules_mod):
        for name in forbidden:
            assert name not in module.__dict__
            assert not any(
                key.startswith(name) for key in module.__dict__
            )


# ---------------------------------------------------------------------------
# Persistence / pipeline integration
# ---------------------------------------------------------------------------


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
    *,
    policy_version_id: UUID | None = None,
    policy_document_id: UUID | None = None,
    stored_file_ids: list[UUID] | None = None,
) -> None:
    await _cleanup_verification(db_conn, application_id)
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
    if stored_file_ids:
        await db_conn.execute(
            "ALTER TABLE stored_files DISABLE TRIGGER stored_files_immutable_trg"
        )
        for file_id in stored_file_ids:
            await db_conn.execute(
                "DELETE FROM stored_files WHERE id = %s",
                (file_id,),
            )
        await db_conn.execute(
            "ALTER TABLE stored_files ENABLE TRIGGER stored_files_immutable_trg"
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
        ("Verification Test Policy", "synthetic_demo", admin_user_id, now),
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
    policy_version_id = ver_row["id"]

    await db_conn.execute(
        """
        INSERT INTO policy_threshold_configs (
            policy_version_id,
            applicant_type,
            min_monthly_income,
            max_foir_pct,
            min_employment_months,
            max_loan_amount,
            created_at
        ) VALUES (
            %s, 'salaried'::applicant_type,
            %s, %s, 12, %s, %s
        )
        """,
        (
            policy_version_id,
            Decimal("25000.00"),
            Decimal("50.00"),
            Decimal("2500000.00"),
            now,
        ),
    )
    for doc_type in ("pan_card", "salary_slip", "bank_statement"):
        await db_conn.execute(
            """
            INSERT INTO policy_document_requirements (
                policy_version_id,
                applicant_type,
                document_type,
                is_mandatory,
                created_at
            ) VALUES (
                %s, 'salaried'::applicant_type,
                %s::document_type, true, %s
            )
            """,
            (policy_version_id, doc_type, now),
        )
    await db_conn.commit()
    return policy_document_id, policy_version_id


async def _insert_application_with_applicant(
    db_conn: AsyncConnection[Any],
    *,
    created_by_user_id: UUID,
    policy_version_id: UUID,
) -> UUID:
    now = datetime.now(timezone.utc)
    ref = f"LL-VER-{uuid4().hex[:10].upper()}"
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
            ref,
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
            "Test Applicant",
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


async def _insert_snapshot(
    db_conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    policy_version_id: UUID,
    created_by_user_id: UUID,
    document_inventory: list[dict[str, Any]],
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
            Jsonb(document_inventory),
            policy_version_id,
            created_by_user_id,
            now,
        ),
    )
    result = await row.fetchone()
    assert result is not None
    await db_conn.commit()
    return result["id"]


async def _insert_running_claim(
    db_conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    input_snapshot_id: UUID,
    claim_fence: int = 3,
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
            locked_by,
            locked_at,
            lease_expires_at,
            started_at,
            created_at
        ) VALUES (
            %s, %s, 'running'::processing_run_status, 1, 'submit',
            %s, 'worker-verify-test', %s, %s, %s, %s
        )
        RETURNING id
        """,
        (
            application_id,
            input_snapshot_id,
            claim_fence,
            now,
            now + timedelta(minutes=10),
            now,
            now,
        ),
    )
    result = await row.fetchone()
    assert result is not None
    await db_conn.commit()
    return result["id"]


async def _insert_extracted_pan(
    db_conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    processing_run_id: UUID,
    uploaded_by_user_id: UUID,
) -> tuple[UUID, UUID]:
    now = datetime.now(timezone.utc)
    digest = uuid4().hex + uuid4().hex
    file_row = await db_conn.execute(
        """
        INSERT INTO stored_files (
            content_sha256, storage_uri, original_filename, mime_type,
            byte_size, signature_ok, malware_scanner, malware_scanned_at,
            malware_signature_version, created_at
        ) VALUES (
            %s, %s, 'pan.txt', 'text/plain', 128, true, 'clamav',
            %s, 'daily.cvd', %s
        )
        RETURNING id
        """,
        (digest, f"file:///tmp/{digest[:16]}.txt", now, now),
    )
    file = await file_row.fetchone()
    assert file is not None
    stored_file_id = file["id"]

    doc_row = await db_conn.execute(
        """
        INSERT INTO application_documents (
            application_id, stored_file_id, content_sha256, document_type,
            status, classification_confidence, classification_method,
            uploaded_by_user_id, created_at, updated_at
        ) VALUES (
            %s, %s, %s, 'pan_card'::document_type,
            'extracted'::document_status, %s, 'heuristic_rules',
            %s, %s, %s
        )
        RETURNING id
        """,
        (
            application_id,
            stored_file_id,
            digest,
            Decimal("0.9500"),
            uploaded_by_user_id,
            now,
            now,
        ),
    )
    doc = await doc_row.fetchone()
    assert doc is not None
    document_id = doc["id"]

    ext_row = await db_conn.execute(
        """
        INSERT INTO document_extractions (
            application_id, application_document_id, processing_run_id,
            extractor_name, extractor_version, status, raw_payload_json,
            created_at, updated_at
        ) VALUES (
            %s, %s, %s, 'pan_card_extractor', '1.0.0',
            'completed'::extraction_status, '{}'::jsonb, %s, %s
        )
        RETURNING id
        """,
        (application_id, document_id, processing_run_id, now, now),
    )
    ext = await ext_row.fetchone()
    assert ext is not None
    extraction_id = ext["id"]

    for field_name, raw, normalized in (
        ("applicant_name", "Test Applicant", {"value": "Test Applicant"}),
        ("pan", "ABCDE1234F", {"value": "ABCDE1234F"}),
        ("date_of_birth", "15/08/1990", {"value": "1990-08-15"}),
    ):
        await db_conn.execute(
            """
            INSERT INTO document_extracted_fields (
                application_id, extraction_id, field_name,
                raw_value, normalized_value_json, confidence, created_at
            ) VALUES (
                %s, %s, %s::extraction_field_name,
                %s, %s, %s, %s
            )
            """,
            (
                application_id,
                extraction_id,
                field_name,
                raw,
                Jsonb(normalized),
                Decimal("0.9900"),
                now,
            ),
        )
    await db_conn.commit()
    return document_id, stored_file_id


@pytest.mark.asyncio
async def test_persist_verification_run_and_findings_immutable(
    db_conn: AsyncConnection[Any],
) -> None:
    admin = await _insert_test_user(
        db_conn,
        email="verify.persist.admin@example.com",
        full_name="Verify Persist Admin",
        role="admin",
        password="admin-password-not-a-secret",
    )
    officer = await _insert_test_user(
        db_conn,
        email="verify.persist.officer@example.com",
        full_name="Verify Persist Officer",
        role="loan_officer",
        password="officer-password-not-a-secret",
    )
    policy_document_id: UUID | None = None
    policy_version_id: UUID | None = None
    application_id: UUID | None = None
    stored_file_ids: list[UUID] = []

    try:
        policy_document_id, policy_version_id = await _seed_policy(
            db_conn,
            admin_user_id=admin["id"],
        )
        application_id = await _insert_application_with_applicant(
            db_conn,
            created_by_user_id=officer["id"],
            policy_version_id=policy_version_id,
        )
        inventory = [
            {"document_type": "pan_card"},
            {"document_type": "salary_slip"},
            {"document_type": "bank_statement"},
        ]
        snapshot_id = await _insert_snapshot(
            db_conn,
            application_id=application_id,
            policy_version_id=policy_version_id,
            created_by_user_id=officer["id"],
            document_inventory=inventory,
        )
        run_id = await _insert_running_claim(
            db_conn,
            application_id=application_id,
            input_snapshot_id=snapshot_id,
            claim_fence=7,
        )
        _doc_id, stored_file_id = await _insert_extracted_pan(
            db_conn,
            application_id=application_id,
            processing_run_id=run_id,
            uploaded_by_user_id=officer["id"],
        )
        stored_file_ids.append(stored_file_id)

        context = build_context_from_parts(
            snapshot=_snapshot(document_inventory=inventory),
            fields=[
                LayeredField(
                    field_name="applicant_name",
                    normalized_value={"value": "Test Applicant"},
                ),
                LayeredField(
                    field_name="pan",
                    normalized_value={"value": "ABCDE1234F"},
                ),
                LayeredField(
                    field_name="date_of_birth",
                    normalized_value={"value": "1990-08-15"},
                ),
                LayeredField(
                    field_name="employer_name",
                    normalized_value={"value": "Acme Corp"},
                ),
            ],
            policy_thresholds=_thresholds(),
            salary_slip_net_pays=[Decimal("60000.00")],
            salary_credits=[
                SalaryCreditObservation(
                    statement_month=date(2026, 1, 1),
                    amount=Decimal("60000.00"),
                )
            ],
            statement_periods=[
                StatementPeriod(
                    start_date=date(2026, 1, 1),
                    end_date=date(2026, 1, 31),
                )
            ],
            mandatory_documents=[
                MandatoryDocumentRequirement(document_type="pan_card"),
                MandatoryDocumentRequirement(document_type="salary_slip"),
                MandatoryDocumentRequirement(document_type="bank_statement"),
            ],
        )
        results = evaluate_all_rules(context)
        assert len(results) == 11

        async with db_conn.transaction():
            run, findings = await persist_verification_results(
                db_conn,
                application_id=application_id,
                processing_run_id=run_id,
                input_snapshot_id=snapshot_id,
                results=results,
                claim_fence=7,
            )

        assert run.total_rules_evaluated == 11
        assert len(findings) == 11
        assert run.passed_count + run.flagged_count + run.failed_count == 11

        with pytest.raises(InsufficientPrivilege):
            await db_conn.execute(
                "UPDATE verification_runs SET passed_count = 0 WHERE id = %s",
                (run.id,),
            )
        await db_conn.rollback()

        with pytest.raises(InsufficientPrivilege):
            await db_conn.execute(
                "DELETE FROM verification_findings WHERE verification_run_id = %s",
                (run.id,),
            )
        await db_conn.rollback()

        # Finish first run so a second active claim can be inserted.
        await db_conn.execute(
            """
            UPDATE processing_runs
               SET status = 'succeeded'::processing_run_status,
                   finished_at = %s
             WHERE id = %s
            """,
            (datetime.now(timezone.utc), run_id),
        )
        await db_conn.commit()

        run_id_2 = await _insert_running_claim(
            db_conn,
            application_id=application_id,
            input_snapshot_id=snapshot_id,
            claim_fence=8,
        )
        _doc2, stored2 = await _insert_extracted_pan(
            db_conn,
            application_id=application_id,
            processing_run_id=run_id_2,
            uploaded_by_user_id=officer["id"],
        )
        stored_file_ids.append(stored2)

        get_settings.cache_clear()
        pool = create_pool(get_settings().DATABASE_URL)
        await pool.open()
        try:
            state = await verify_rules(
                {
                    "_db_pool": pool,
                    "application_id": str(application_id),
                    "processing_run_id": str(run_id_2),
                    "input_snapshot_id": str(snapshot_id),
                    "claim_fence": 8,
                    "field_corrections": {
                        "pan": {"value": "ABCDE1234F"},
                    },
                }
            )
            assert state["verification_run_id"]
            assert state["verification_summary"]["total_rules_evaluated"] == 11
            assert len(state["verification_findings"]) == 11
        finally:
            await pool.close()
    finally:
        if application_id is not None:
            await _cleanup_application(
                db_conn,
                application_id,
                policy_version_id=policy_version_id,
                policy_document_id=policy_document_id,
                stored_file_ids=stored_file_ids,
            )
        await _purge_user(db_conn, officer["id"])
        await _purge_user(db_conn, admin["id"])
