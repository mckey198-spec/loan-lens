"""Schema tests for application financial calculations (M09-T01)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from psycopg import AsyncConnection
from psycopg.errors import ForeignKeyViolation, InsufficientPrivilege, UniqueViolation
from psycopg.types.json import Jsonb

from tests.conftest import _insert_test_user, _purge_user

_HASH_A = "a" * 64
_HASH_B = "b" * 64


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
    await _cleanup_financial_calculations(db_conn, application_id)
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


async def _insert_financial_calculation(
    db_conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    processing_run_id: UUID,
    input_snapshot_id: UUID,
    proposed_emi: Decimal = Decimal("15000.00"),
    total_obligations: Decimal = Decimal("20000.00"),
    foir_pct: Decimal = Decimal("33.33"),
    foir_threshold_pct: Decimal = Decimal("50.00"),
    foir_passed: bool = True,
    net_disposable_income: Decimal = Decimal("40000.00"),
    max_qualifying_loan_amount: Decimal = Decimal("1200000.00"),
    calculation_details_json: dict[str, Any] | None = None,
    claim_fence: int = 0,
) -> UUID:
    details = calculation_details_json if calculation_details_json is not None else {}
    row = await db_conn.execute(
        """
        INSERT INTO application_financial_calculations (
            application_id,
            processing_run_id,
            input_snapshot_id,
            proposed_emi,
            total_obligations,
            foir_pct,
            foir_threshold_pct,
            foir_passed,
            net_disposable_income,
            max_qualifying_loan_amount,
            calculation_details_json,
            claim_fence
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        RETURNING id
        """,
        (
            application_id,
            processing_run_id,
            input_snapshot_id,
            proposed_emi,
            total_obligations,
            foir_pct,
            foir_threshold_pct,
            foir_passed,
            net_disposable_income,
            max_qualifying_loan_amount,
            Jsonb(details),
            claim_fence,
        ),
    )
    result = await row.fetchone()
    assert result is not None
    return result["id"]


@pytest.mark.asyncio
async def test_insert_valid_financial_calculation_with_composite_fks(
    db_conn: AsyncConnection[Any],
) -> None:
    user = await _insert_test_user(
        db_conn,
        email="fin.calc.schema.insert@example.com",
        full_name="Fin Calc Insert",
        role="loan_officer",
        password="test-password-not-a-secret",
    )
    application_id: UUID | None = None
    policy_document_id: UUID | None = None
    try:
        application_id = await _insert_draft_application(
            db_conn,
            created_by_user_id=user["id"],
            reference_code=f"LL-FINI-{uuid4().hex[:6].upper()}",
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
        calc_id = await _insert_financial_calculation(
            db_conn,
            application_id=application_id,
            processing_run_id=run_id,
            input_snapshot_id=snapshot_id,
            calculation_details_json={"formula": "emi_standard_v1"},
            claim_fence=0,
        )
        await db_conn.commit()

        row = await db_conn.execute(
            """
            SELECT id,
                   application_id,
                   processing_run_id,
                   input_snapshot_id,
                   proposed_emi,
                   total_obligations,
                   foir_pct,
                   foir_threshold_pct,
                   foir_passed,
                   net_disposable_income,
                   max_qualifying_loan_amount,
                   calculation_details_json,
                   claim_fence
              FROM application_financial_calculations
             WHERE id = %s
            """,
            (calc_id,),
        )
        created = await row.fetchone()
        assert created is not None
        assert created["application_id"] == application_id
        assert created["processing_run_id"] == run_id
        assert created["input_snapshot_id"] == snapshot_id
        assert created["proposed_emi"] == Decimal("15000.00")
        assert created["total_obligations"] == Decimal("20000.00")
        assert created["foir_pct"] == Decimal("33.33")
        assert created["foir_threshold_pct"] == Decimal("50.00")
        assert created["foir_passed"] is True
        assert created["net_disposable_income"] == Decimal("40000.00")
        assert created["max_qualifying_loan_amount"] == Decimal("1200000.00")
        assert created["calculation_details_json"] == {"formula": "emi_standard_v1"}
        assert created["claim_fence"] == 0
    finally:
        await db_conn.rollback()
        if application_id is not None:
            await _cleanup_application(db_conn, application_id)
        if policy_document_id is not None:
            await _cleanup_policy_document(db_conn, policy_document_id)
        await _purge_user(db_conn, user["id"])


@pytest.mark.asyncio
async def test_reject_insert_when_composite_keys_mismatch_application_id(
    db_conn: AsyncConnection[Any],
) -> None:
    user = await _insert_test_user(
        db_conn,
        email="fin.calc.schema.fk@example.com",
        full_name="Fin Calc FK",
        role="loan_officer",
        password="test-password-not-a-secret",
    )
    application_a: UUID | None = None
    application_b: UUID | None = None
    policy_document_id: UUID | None = None
    try:
        application_a = await _insert_draft_application(
            db_conn,
            created_by_user_id=user["id"],
            reference_code=f"LL-FINA-{uuid4().hex[:6].upper()}",
        )
        application_b = await _insert_draft_application(
            db_conn,
            created_by_user_id=user["id"],
            reference_code=f"LL-FINB-{uuid4().hex[:6].upper()}",
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

        # App A cannot bind a processing run that belongs to app B.
        with pytest.raises(ForeignKeyViolation):
            await _insert_financial_calculation(
                db_conn,
                application_id=application_a,
                processing_run_id=run_b,
                input_snapshot_id=snapshot_a,
            )
            await db_conn.commit()
        await db_conn.rollback()

        # App A cannot bind an input snapshot that belongs to app B.
        with pytest.raises(ForeignKeyViolation):
            await _insert_financial_calculation(
                db_conn,
                application_id=application_a,
                processing_run_id=run_a,
                input_snapshot_id=snapshot_b,
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
async def test_uq_fin_calc_proc_rejects_duplicate_processing_run(
    db_conn: AsyncConnection[Any],
) -> None:
    user = await _insert_test_user(
        db_conn,
        email="fin.calc.schema.uq@example.com",
        full_name="Fin Calc Unique",
        role="loan_officer",
        password="test-password-not-a-secret",
    )
    application_id: UUID | None = None
    policy_document_id: UUID | None = None
    try:
        application_id = await _insert_draft_application(
            db_conn,
            created_by_user_id=user["id"],
            reference_code=f"LL-FINU-{uuid4().hex[:6].upper()}",
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
        await _insert_financial_calculation(
            db_conn,
            application_id=application_id,
            processing_run_id=run_id,
            input_snapshot_id=snapshot_id,
        )
        await db_conn.commit()

        with pytest.raises(UniqueViolation):
            await _insert_financial_calculation(
                db_conn,
                application_id=application_id,
                processing_run_id=run_id,
                input_snapshot_id=snapshot_id,
                proposed_emi=Decimal("16000.00"),
            )
            await db_conn.commit()
        await db_conn.rollback()
    finally:
        await db_conn.rollback()
        if application_id is not None:
            await _cleanup_application(db_conn, application_id)
        if policy_document_id is not None:
            await _cleanup_policy_document(db_conn, policy_document_id)
        await _purge_user(db_conn, user["id"])


@pytest.mark.asyncio
async def test_financial_calculations_reject_update_and_delete(
    db_conn: AsyncConnection[Any],
) -> None:
    user = await _insert_test_user(
        db_conn,
        email="fin.calc.schema.immut@example.com",
        full_name="Fin Calc Immut",
        role="loan_officer",
        password="test-password-not-a-secret",
    )
    application_id: UUID | None = None
    policy_document_id: UUID | None = None
    try:
        application_id = await _insert_draft_application(
            db_conn,
            created_by_user_id=user["id"],
            reference_code=f"LL-FINM-{uuid4().hex[:6].upper()}",
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
        calc_id = await _insert_financial_calculation(
            db_conn,
            application_id=application_id,
            processing_run_id=run_id,
            input_snapshot_id=snapshot_id,
            proposed_emi=Decimal("15000.00"),
        )
        await db_conn.commit()

        with pytest.raises(InsufficientPrivilege):
            await db_conn.execute(
                """
                UPDATE application_financial_calculations
                   SET proposed_emi = %s
                 WHERE id = %s
                """,
                (Decimal("1.00"), calc_id),
            )
            await db_conn.commit()
        await db_conn.rollback()

        with pytest.raises(InsufficientPrivilege):
            await db_conn.execute(
                "DELETE FROM application_financial_calculations WHERE id = %s",
                (calc_id,),
            )
            await db_conn.commit()
        await db_conn.rollback()

        retained = await db_conn.execute(
            """
            SELECT proposed_emi
              FROM application_financial_calculations
             WHERE id = %s
            """,
            (calc_id,),
        )
        row = await retained.fetchone()
        assert row is not None
        assert row["proposed_emi"] == Decimal("15000.00")
    finally:
        await db_conn.rollback()
        if application_id is not None:
            await _cleanup_application(db_conn, application_id)
        if policy_document_id is not None:
            await _cleanup_policy_document(db_conn, policy_document_id)
        await _purge_user(db_conn, user["id"])
