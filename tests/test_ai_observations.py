"""AI observations schema, engine, and API tests (M11 / BR-12)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from psycopg import AsyncConnection
from psycopg.errors import CheckViolation, ForeignKeyViolation, InsufficientPrivilege, UniqueViolation
from psycopg.types.json import Jsonb

from apps.api.main import app
from modules.observations.engine import (
    build_observation_context,
    generate_observations,
)
from modules.observations.provider import (
    DETERMINISTIC_MODEL_ID,
    DeterministicObservationProvider,
)
from modules.observations.schemas import ObservationOutput
from modules.observations.service import run_observation_generation
from modules.worker.runner import PLACEHOLDER_PIPELINE
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


async def _cleanup_ai_observations(
    db_conn: AsyncConnection[Any],
    application_id: UUID,
) -> None:
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
    await db_conn.commit()


async def _cleanup_application(
    db_conn: AsyncConnection[Any],
    application_id: UUID,
    *,
    policy_version_id: UUID | None = None,
    policy_document_id: UUID | None = None,
) -> None:
    await _cleanup_ai_observations(db_conn, application_id)
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
        ("AI Obs Test Policy", "synthetic_demo", admin_user_id, now),
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


async def _insert_policy_chunk(
    db_conn: AsyncConnection[Any],
    *,
    policy_version_id: UUID,
    chunk_index: int = 0,
    section_label: str = "FOIR Limits",
    content_text: str = "Maximum FOIR is fifty percent for salaried applicants.",
) -> UUID:
    now = datetime.now(timezone.utc)
    row = await db_conn.execute(
        """
        INSERT INTO policy_chunks (
            policy_version_id,
            chunk_index,
            section_label,
            content_text,
            created_at
        ) VALUES (%s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            policy_version_id,
            chunk_index,
            section_label,
            content_text,
            now,
        ),
    )
    inserted = await row.fetchone()
    assert inserted is not None
    await db_conn.commit()
    return inserted["id"]


async def _insert_application(
    db_conn: AsyncConnection[Any],
    *,
    created_by_user_id: UUID,
    policy_version_id: UUID,
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
            applicable_policy_version_id,
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
            %s,
            %s
        )
        RETURNING id
        """,
        (
            f"LL-AIO-{uuid4().hex[:8].upper()}",
            created_by_user_id,
            Decimal("500000.00"),
            policy_version_id,
            now,
            now,
        ),
    )
    result = await row.fetchone()
    assert result is not None
    await db_conn.commit()
    return result["id"]


async def _insert_snapshot_and_run(
    db_conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    policy_version_id: UUID,
    created_by_user_id: UUID,
    snapshot_number: int = 1,
    snapshot_hash: str = _HASH_A,
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
    snap_row = await snap.fetchone()
    assert snap_row is not None
    snapshot_id = snap_row["id"]

    run = await db_conn.execute(
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
            'succeeded'::processing_run_status,
            1,
            'submit',
            0,
            %s,
            %s
        )
        RETURNING id
        """,
        (application_id, snapshot_id, now, now),
    )
    run_row = await run.fetchone()
    assert run_row is not None
    await db_conn.commit()
    return snapshot_id, run_row["id"]


async def _insert_ai_observation(
    db_conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    processing_run_id: UUID,
    model_id: str = DETERMINISTIC_MODEL_ID,
    observation_text: str = "Advisory AI observation only.",
    risk_factors: list[str] | None = None,
    strengths: list[str] | None = None,
    cited_chunk_ids: list[str] | None = None,
    claim_fence: int = 0,
    is_ai_generated: bool = True,
) -> UUID:
    row = await db_conn.execute(
        """
        INSERT INTO assessment_ai_observations (
            application_id,
            processing_run_id,
            model_id,
            observation_text,
            risk_factors_json,
            strengths_json,
            cited_chunk_ids_json,
            is_ai_generated,
            claim_fence
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        RETURNING id
        """,
        (
            application_id,
            processing_run_id,
            model_id,
            observation_text,
            Jsonb(risk_factors or []),
            Jsonb(strengths or []),
            Jsonb(cited_chunk_ids or []),
            is_ai_generated,
            claim_fence,
        ),
    )
    result = await row.fetchone()
    assert result is not None
    return result["id"]


def test_pipeline_wires_observe_after_policy_retrieval() -> None:
    names = [name for name, _handler in PLACEHOLDER_PIPELINE]
    assert "observe" in names
    assert names.index("observe") == names.index("policy_retrieval") + 1
    assert names[-1] == "observe"


def test_deterministic_provider_synthesizes_findings_and_ratios() -> None:
    chunk_id = uuid4()
    context = build_observation_context(
        application_id=uuid4(),
        processing_run_id=uuid4(),
        snapshot_facts={
            "applicant_type": "salaried",
            "requested_amount": Decimal("500000.00"),
            "tenure_months": 36,
            "declared_net_monthly_income": Decimal("60000.00"),
        },
        verification_findings=[
            {
                "rule_code": "BR-02",
                "rule_name": "PAN Match",
                "status": "failed",
                "severity": "critical",
                "message": "PAN mismatch across documents",
            },
            {
                "rule_code": "BR-01",
                "rule_name": "Name Match",
                "status": "passed",
                "severity": "info",
                "message": "Names align",
            },
        ],
        financial_calculation={
            "proposed_emi": Decimal("16251.22"),
            "total_obligations": Decimal("21251.22"),
            "foir_pct": Decimal("55.25"),
            "foir_threshold_pct": Decimal("50.00"),
            "foir_passed": False,
            "net_disposable_income": Decimal("-1251.22"),
            "max_qualifying_loan_amount": Decimal("0.00"),
        },
        policy_citations={
            "foir": [
                {
                    "chunk_id": str(chunk_id),
                    "section_label": "FOIR Limits",
                    "rank_score": 0.9,
                }
            ]
        },
    )
    output = generate_observations(
        context,
        provider=DeterministicObservationProvider(),
    )

    assert isinstance(output, ObservationOutput)
    assert output.model_id == DETERMINISTIC_MODEL_ID
    assert "Advisory AI observation only" in output.observation_text
    assert "approval" in output.observation_text.lower()
    assert any("BR-02" in item for item in output.risk_factors)
    assert any("FOIR" in item for item in output.risk_factors)
    assert any("BR-01" in item for item in output.strengths)
    assert chunk_id in output.cited_chunk_ids
    # Advisory: must not invent a decision or grade token as the outcome.
    lowered = output.observation_text.lower()
    assert "approved" not in lowered
    assert "rejected" not in lowered
    assert "risk grade" not in lowered or "does not assign a risk grade" in lowered


@pytest.mark.asyncio
async def test_schema_unique_run_composite_fk_and_append_only(
    db_conn: AsyncConnection[Any],
) -> None:
    user = await _insert_test_user(
        db_conn,
        email="ai.obs.schema@example.com",
        full_name="AI Obs Schema",
        role="loan_officer",
        password="test-password-not-a-secret",
    )
    application_a: UUID | None = None
    application_b: UUID | None = None
    policy_document_id: UUID | None = None
    policy_version_id: UUID | None = None
    try:
        policy_document_id, policy_version_id = await _seed_policy(
            db_conn,
            admin_user_id=user["id"],
        )
        application_a = await _insert_application(
            db_conn,
            created_by_user_id=user["id"],
            policy_version_id=policy_version_id,
        )
        application_b = await _insert_application(
            db_conn,
            created_by_user_id=user["id"],
            policy_version_id=policy_version_id,
        )
        _snap_a, run_a = await _insert_snapshot_and_run(
            db_conn,
            application_id=application_a,
            policy_version_id=policy_version_id,
            created_by_user_id=user["id"],
            snapshot_hash=_HASH_A,
        )
        _snap_b, run_b = await _insert_snapshot_and_run(
            db_conn,
            application_id=application_b,
            policy_version_id=policy_version_id,
            created_by_user_id=user["id"],
            snapshot_hash=_HASH_B,
        )

        obs_id = await _insert_ai_observation(
            db_conn,
            application_id=application_a,
            processing_run_id=run_a,
            claim_fence=3,
            risk_factors=["BR-02 failed"],
            strengths=["FOIR within threshold"],
        )
        await db_conn.commit()

        stored = await db_conn.execute(
            """
            SELECT is_ai_generated, claim_fence, processing_run_id
              FROM assessment_ai_observations
             WHERE id = %s
            """,
            (obs_id,),
        )
        row = await stored.fetchone()
        assert row is not None
        assert row["is_ai_generated"] is True
        assert row["claim_fence"] == 3
        assert row["processing_run_id"] == run_a

        with pytest.raises(UniqueViolation):
            await _insert_ai_observation(
                db_conn,
                application_id=application_a,
                processing_run_id=run_a,
                observation_text="Duplicate for same run",
            )
            await db_conn.commit()
        await db_conn.rollback()

        with pytest.raises(ForeignKeyViolation):
            await _insert_ai_observation(
                db_conn,
                application_id=application_a,
                processing_run_id=run_b,
                observation_text="Cross-application composite FK must fail",
            )
            await db_conn.commit()
        await db_conn.rollback()

        with pytest.raises(CheckViolation):
            await _insert_ai_observation(
                db_conn,
                application_id=application_b,
                processing_run_id=run_b,
                is_ai_generated=False,
                observation_text="Must remain AI-generated",
            )
            await db_conn.commit()
        await db_conn.rollback()

        with pytest.raises(InsufficientPrivilege):
            await db_conn.execute(
                """
                UPDATE assessment_ai_observations
                   SET observation_text = %s
                 WHERE id = %s
                """,
                ("mutated", obs_id),
            )
            await db_conn.commit()
        await db_conn.rollback()

        with pytest.raises(InsufficientPrivilege):
            await db_conn.execute(
                "DELETE FROM assessment_ai_observations WHERE id = %s",
                (obs_id,),
            )
            await db_conn.commit()
        await db_conn.rollback()
    finally:
        await db_conn.rollback()
        if application_a is not None:
            await _cleanup_application(
                db_conn,
                application_a,
                policy_version_id=None,
                policy_document_id=None,
            )
        if application_b is not None:
            await _cleanup_application(
                db_conn,
                application_b,
                policy_version_id=policy_version_id,
                policy_document_id=policy_document_id,
            )
        await _purge_user(db_conn, user["id"])


@pytest.mark.asyncio
async def test_persist_observation_preserves_ai_label_and_cited_chunks(
    db_conn: AsyncConnection[Any],
) -> None:
    user = await _insert_test_user(
        db_conn,
        email="ai.obs.persist@example.com",
        full_name="AI Obs Persist",
        role="loan_officer",
        password="test-password-not-a-secret",
    )
    application_id: UUID | None = None
    policy_document_id: UUID | None = None
    policy_version_id: UUID | None = None
    try:
        policy_document_id, policy_version_id = await _seed_policy(
            db_conn,
            admin_user_id=user["id"],
        )
        chunk_id = await _insert_policy_chunk(
            db_conn,
            policy_version_id=policy_version_id,
        )
        application_id = await _insert_application(
            db_conn,
            created_by_user_id=user["id"],
            policy_version_id=policy_version_id,
        )
        snapshot_id, run_id = await _insert_snapshot_and_run(
            db_conn,
            application_id=application_id,
            policy_version_id=policy_version_id,
            created_by_user_id=user["id"],
        )

        async with db_conn.transaction():
            _context, output, record = await run_observation_generation(
                db_conn,
                application_id=application_id,
                processing_run_id=run_id,
                input_snapshot_id=snapshot_id,
                claim_fence=7,
                verification_findings=[
                    {
                        "rule_code": "BR-08",
                        "rule_name": "Min Income",
                        "status": "flagged",
                        "severity": "high",
                        "message": "Income near minimum threshold",
                    }
                ],
                financial_calculation={
                    "proposed_emi": "16251.22",
                    "total_obligations": "21251.22",
                    "foir_pct": "35.42",
                    "foir_threshold_pct": "50.00",
                    "foir_passed": True,
                    "net_disposable_income": "38748.78",
                    "max_qualifying_loan_amount": "900000.00",
                },
                policy_citations={
                    "income": [
                        {
                            "chunk_id": str(chunk_id),
                            "section_label": "FOIR Limits",
                            "rank_score": 1.0,
                        }
                    ]
                },
                provider=DeterministicObservationProvider(),
            )

        assert record.is_ai_generated is True
        assert record.claim_fence == 7
        assert record.model_id == DETERMINISTIC_MODEL_ID
        assert chunk_id in output.cited_chunk_ids
        assert any("BR-08" in item for item in output.risk_factors)
        assert any("FOIR" in item for item in output.strengths)

        stored = await db_conn.execute(
            """
            SELECT is_ai_generated,
                   cited_chunk_ids_json,
                   risk_factors_json,
                   claim_fence
              FROM assessment_ai_observations
             WHERE id = %s
            """,
            (record.id,),
        )
        row = await stored.fetchone()
        assert row is not None
        assert row["is_ai_generated"] is True
        assert row["claim_fence"] == 7
        assert str(chunk_id) in row["cited_chunk_ids_json"]
    finally:
        await db_conn.rollback()
        if application_id is not None:
            await _cleanup_application(
                db_conn,
                application_id,
                policy_version_id=policy_version_id,
                policy_document_id=policy_document_id,
            )
        await _purge_user(db_conn, user["id"])


@pytest.mark.asyncio
async def test_observations_endpoint_rbac_and_tenant_isolation(
    client: AsyncClient,
    test_user: dict[str, Any],
    reviewer_user: dict[str, Any],
    admin_user: dict[str, Any],
    db_conn: AsyncConnection[Any],
) -> None:
    policy_document_id, policy_version_id = await _seed_policy(
        db_conn,
        admin_user_id=admin_user["id"],
    )
    chunk_id = await _insert_policy_chunk(
        db_conn,
        policy_version_id=policy_version_id,
    )
    application_id = await _insert_application(
        db_conn,
        created_by_user_id=test_user["id"],
        policy_version_id=policy_version_id,
    )
    snapshot_id, run_id = await _insert_snapshot_and_run(
        db_conn,
        application_id=application_id,
        policy_version_id=policy_version_id,
        created_by_user_id=test_user["id"],
    )

    other_app_id: UUID | None = None
    try:
        async with db_conn.transaction():
            _ctx, _out, record = await run_observation_generation(
                db_conn,
                application_id=application_id,
                processing_run_id=run_id,
                input_snapshot_id=snapshot_id,
                claim_fence=1,
                verification_findings=[
                    {
                        "rule_code": "BR-01",
                        "rule_name": "Name Match",
                        "status": "passed",
                        "severity": "info",
                        "message": "Names align",
                    }
                ],
                financial_calculation={
                    "proposed_emi": "10000.00",
                    "foir_pct": "40.00",
                    "foir_threshold_pct": "50.00",
                    "foir_passed": True,
                    "net_disposable_income": "20000.00",
                },
                policy_citations=[
                    {
                        "chunk_id": str(chunk_id),
                        "topic": "foir",
                        "section_label": "FOIR Limits",
                    }
                ],
            )

        path = f"/api/v1/applications/{application_id}/observations"

        unauth = await client.get(path)
        assert unauth.status_code == 401, unauth.text

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
                assert body["id"] == str(record.id)
                assert body["application_id"] == str(application_id)
                assert body["processing_run_id"] == str(run_id)
                assert body["is_ai_generated"] is True
                assert str(chunk_id) in [
                    str(cid) for cid in body["cited_chunk_ids"]
                ]

        other_app_id = await _insert_application(
            db_conn,
            created_by_user_id=test_user["id"],
            policy_version_id=policy_version_id,
        )
        await _login(
            client,
            email=test_user["email"],
            password=test_user["password"],
        )
        isolated = await client.get(
            f"/api/v1/applications/{other_app_id}/observations"
        )
        assert isolated.status_code == 404, isolated.text
        assert isolated.json()["code"] == "AI_OBSERVATION_NOT_FOUND"

        missing = await client.get(
            f"/api/v1/applications/{uuid4()}/observations"
        )
        assert missing.status_code == 404, missing.text
        assert missing.json()["code"] == "AI_OBSERVATION_NOT_FOUND"
    finally:
        await _cleanup_application(
            db_conn,
            application_id,
            policy_version_id=None,
            policy_document_id=None,
        )
        if other_app_id is not None:
            await _cleanup_application(
                db_conn,
                other_app_id,
                policy_version_id=None,
                policy_document_id=None,
            )
        await db_conn.execute(
            "DELETE FROM policy_chunks WHERE policy_version_id = %s",
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
        await db_conn.execute(
            "DELETE FROM policy_documents WHERE id = %s",
            (policy_document_id,),
        )
        await db_conn.commit()
