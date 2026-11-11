"""Deterministic financial calculation engine and pipeline node (M09-T02)."""

from __future__ import annotations

import ast
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from psycopg import AsyncConnection

from modules.calculations.engine import (
    compute_financial_metrics,
    compute_max_qualifying_loan_amount,
    compute_proposed_emi,
)
from modules.calculations.schemas import CalculationInputs
from modules.worker.nodes.calculate import calculate_financials
from shared.db import create_pool
from shared.settings import get_settings
from tests.conftest import _insert_test_user, _purge_user

_HASH_A = "a" * 64
_ENGINE_PATH = (
    Path(__file__).resolve().parents[1]
    / "modules"
    / "calculations"
    / "engine.py"
)


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _sample_inputs(
    *,
    monthly_net_income: Decimal = Decimal("60000.00"),
    monthly_obligations: Decimal = Decimal("5000.00"),
    requested_loan_amount: Decimal = Decimal("500000.00"),
    tenure_months: int = 36,
    annual_interest_rate_pct: Decimal = Decimal("10.50"),
    max_allowed_foir_pct: Decimal = Decimal("50.00"),
    monthly_gross_income: Decimal | None = None,
) -> CalculationInputs:
    gross = (
        monthly_gross_income
        if monthly_gross_income is not None
        else monthly_net_income + Decimal("15000.00")
    )
    return CalculationInputs(
        monthly_gross_income=gross,
        monthly_net_income=monthly_net_income,
        monthly_obligations=monthly_obligations,
        requested_loan_amount=requested_loan_amount,
        tenure_months=tenure_months,
        annual_interest_rate_pct=annual_interest_rate_pct,
        max_allowed_foir_pct=max_allowed_foir_pct,
    )


def test_engine_uses_no_float_literals_or_calls() -> None:
    tree = ast.parse(_ENGINE_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            pytest.fail(f"float literal at line {node.lineno}")
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "float":
                pytest.fail(f"float() call at line {node.lineno}")


def test_emi_for_known_lending_case() -> None:
    """₹5,00,000 at 10.5% for 36 months → ₹16,251.22."""
    emi = compute_proposed_emi(
        Decimal("500000.00"),
        Decimal("10.50"),
        36,
    )
    assert emi == Decimal("16251.22")
    assert not isinstance(emi, float)


def test_zero_interest_emi_uses_principal_over_tenure() -> None:
    emi = compute_proposed_emi(Decimal("360000.00"), Decimal("0.00"), 36)
    assert emi == Decimal("10000.00")


def test_foir_pass_boundary_and_fail() -> None:
    # Pass: FOIR exactly at threshold.
    # total_obligations / income * 100 == 50.00
    # With income 60000, threshold EMI+obl = 30000 → obl 5000 → emi target 25000
    # Use loan sized so proposed EMI yields FOIR == 50.
    max_emi = Decimal("25000.00")
    principal = compute_max_qualifying_loan_amount(
        max_emi, Decimal("10.50"), 36
    )
    boundary = compute_financial_metrics(
        _sample_inputs(
            requested_loan_amount=principal,
            monthly_obligations=Decimal("5000.00"),
            monthly_net_income=Decimal("60000.00"),
            max_allowed_foir_pct=Decimal("50.00"),
        )
    )
    assert boundary.foir_pct == Decimal("50.00")
    assert boundary.foir_passed is True

    # Fail: request above qualifying amount.
    over = compute_financial_metrics(
        _sample_inputs(
            requested_loan_amount=principal + Decimal("10000.00"),
            monthly_obligations=Decimal("5000.00"),
            monthly_net_income=Decimal("60000.00"),
            max_allowed_foir_pct=Decimal("50.00"),
        )
    )
    assert over.foir_pct > Decimal("50.00")
    assert over.foir_passed is False


def test_negative_net_disposable_income() -> None:
    result = compute_financial_metrics(
        _sample_inputs(
            monthly_net_income=Decimal("20000.00"),
            monthly_obligations=Decimal("15000.00"),
            requested_loan_amount=Decimal("500000.00"),
            max_allowed_foir_pct=Decimal("50.00"),
        )
    )
    assert result.proposed_emi == Decimal("16251.22")
    assert result.total_obligations == Decimal("31251.22")
    assert result.net_disposable_income == Decimal("-11251.22")
    assert result.foir_passed is False
    assert result.max_qualifying_loan_amount == Decimal("0.00")


def test_zero_income_foir_is_safe_and_fails() -> None:
    result = compute_financial_metrics(
        _sample_inputs(
            monthly_net_income=Decimal("0.00"),
            monthly_gross_income=Decimal("0.00"),
            monthly_obligations=Decimal("1000.00"),
            requested_loan_amount=Decimal("100000.00"),
            tenure_months=12,
            annual_interest_rate_pct=Decimal("12.00"),
        )
    )
    assert result.foir_pct == Decimal("999.99")
    assert result.foir_passed is False
    assert result.calculation_details["zero_income_foir_safe"] is True


def test_max_qualifying_loan_matches_reverse_amortization() -> None:
    inputs = _sample_inputs()
    result = compute_financial_metrics(inputs)

    max_allowable_emi = _money(
        (inputs.monthly_net_income * inputs.max_allowed_foir_pct / Decimal("100"))
        - inputs.monthly_obligations
    )
    assert max_allowable_emi == Decimal("25000.00")
    assert result.max_qualifying_loan_amount == Decimal("769172.94")

    # Reverse-check: EMI of max qualifying principal equals max allowable EMI.
    check_emi = compute_proposed_emi(
        result.max_qualifying_loan_amount,
        inputs.annual_interest_rate_pct,
        inputs.tenure_months,
    )
    assert check_emi == max_allowable_emi


def test_known_case_full_metric_bundle() -> None:
    result = compute_financial_metrics(_sample_inputs())
    assert result.proposed_emi == Decimal("16251.22")
    assert result.total_obligations == Decimal("21251.22")
    assert result.foir_pct == Decimal("35.42")
    assert result.foir_threshold_pct == Decimal("50.00")
    assert result.foir_passed is True
    assert result.net_disposable_income == Decimal("38748.78")
    assert result.max_qualifying_loan_amount == Decimal("769172.94")
    assert result.calculation_details["formula"] == "standard_amortizing_emi_v1"
    assert result.calculation_details["rounding"] == "ROUND_HALF_UP"


# --- Database / pipeline integration ---------------------------------------


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
    *,
    policy_version_id: UUID | None = None,
    policy_document_id: UUID | None = None,
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


async def _insert_draft_application(
    db_conn: AsyncConnection[Any],
    *,
    created_by_user_id: UUID,
) -> UUID:
    now = datetime.now(timezone.utc)
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
        (
            f"LL-{uuid4().hex[:12].upper()}",
            created_by_user_id,
            Decimal("500000.00"),
            now,
            now,
        ),
    )
    result = await row.fetchone()
    assert result is not None
    return result["id"]


async def _insert_policy(
    db_conn: AsyncConnection[Any],
    *,
    created_by_user_id: UUID,
) -> tuple[UUID, UUID]:
    now = datetime.now(timezone.utc)
    doc = await db_conn.execute(
        """
        INSERT INTO policy_documents (
            title, lender_label, created_by_user_id, created_at
        ) VALUES (%s, %s, %s, %s)
        RETURNING id
        """,
        ("Synthetic Retail Policy", "synthetic_demo", created_by_user_id, now),
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
            %s, %s, 'draft'::policy_status, %s, %s, %s, %s, %s, %s
        )
        RETURNING id
        """,
        (
            policy_document_id,
            "v1",
            Decimal("10.50"),
            date(2026, 1, 1),
            date(2026, 12, 31),
            created_by_user_id,
            now,
            now,
        ),
    )
    ver_row = await ver.fetchone()
    assert ver_row is not None
    return policy_document_id, ver_row["id"]


async def _insert_snapshot_and_inputs(
    db_conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    policy_version_id: UUID,
    created_by_user_id: UUID,
    monthly_net_income: Decimal = Decimal("60000.00"),
    monthly_obligations: Decimal = Decimal("5000.00"),
    requested_loan_amount: Decimal = Decimal("500000.00"),
    max_allowed_foir_pct: Decimal = Decimal("50.00"),
) -> UUID:
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
            %s, 1, %s, %s,
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
            _HASH_A,
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
            max_allowed_foir_pct,
            now,
        ),
    )
    await db_conn.commit()
    return snapshot_id


async def _insert_running_claim(
    db_conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    input_snapshot_id: UUID,
    claim_fence: int = 5,
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
            %s, 'worker-calc-test', %s, %s, %s, %s
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


@pytest.mark.asyncio
async def test_pipeline_node_persists_calculation_with_fence_and_snapshot_fk(
    db_conn: AsyncConnection[Any],
) -> None:
    officer = await _insert_test_user(
        db_conn,
        email=f"calc-officer-{uuid4().hex[:8]}@example.com",
        full_name="Calc Officer",
        role="loan_officer",
        password="test-password-not-a-secret",
    )
    application_id: UUID | None = None
    policy_document_id: UUID | None = None
    policy_version_id: UUID | None = None
    try:
        policy_document_id, policy_version_id = await _insert_policy(
            db_conn, created_by_user_id=officer["id"]
        )
        application_id = await _insert_draft_application(
            db_conn, created_by_user_id=officer["id"]
        )
        await db_conn.execute(
            """
            UPDATE applications
               SET applicable_policy_version_id = %s
             WHERE id = %s
            """,
            (policy_version_id, application_id),
        )
        await db_conn.commit()

        snapshot_id = await _insert_snapshot_and_inputs(
            db_conn,
            application_id=application_id,
            policy_version_id=policy_version_id,
            created_by_user_id=officer["id"],
        )
        claim_fence = 11
        run_id = await _insert_running_claim(
            db_conn,
            application_id=application_id,
            input_snapshot_id=snapshot_id,
            claim_fence=claim_fence,
        )

        get_settings.cache_clear()
        pool = create_pool(get_settings().DATABASE_URL)
        await pool.open()
        try:
            state = await calculate_financials(
                {
                    "_db_pool": pool,
                    "application_id": str(application_id),
                    "processing_run_id": str(run_id),
                    "input_snapshot_id": str(snapshot_id),
                    "claim_fence": claim_fence,
                }
            )
        finally:
            await pool.close()

        assert state["financial_calculation_id"]
        assert state["financial_calculation"]["proposed_emi"] == "16251.22"
        assert state["financial_calculation"]["foir_pct"] == "35.42"
        assert state["financial_calculation"]["foir_passed"] is True

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
             WHERE processing_run_id = %s
            """,
            (run_id,),
        )
        created = await row.fetchone()
        assert created is not None
        assert created["application_id"] == application_id
        assert created["processing_run_id"] == run_id
        assert created["input_snapshot_id"] == snapshot_id
        assert created["claim_fence"] == claim_fence
        assert created["proposed_emi"] == Decimal("16251.22")
        assert created["total_obligations"] == Decimal("21251.22")
        assert created["foir_pct"] == Decimal("35.42")
        assert created["foir_threshold_pct"] == Decimal("50.00")
        assert created["foir_passed"] is True
        assert created["net_disposable_income"] == Decimal("38748.78")
        assert created["max_qualifying_loan_amount"] == Decimal("769172.94")
        assert created["calculation_details_json"]["formula"] == (
            "standard_amortizing_emi_v1"
        )
        assert str(created["id"]) == state["financial_calculation_id"]
    finally:
        if application_id is not None:
            await _cleanup_application(
                db_conn,
                application_id,
                policy_version_id=policy_version_id,
                policy_document_id=policy_document_id,
            )
        await _purge_user(db_conn, officer["id"])
