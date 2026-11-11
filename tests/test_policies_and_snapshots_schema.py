"""Schema tests for policy store and input snapshots (M06-T01)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from psycopg import AsyncConnection
from psycopg.errors import (
    CheckViolation,
    ExclusionViolation,
    ForeignKeyViolation,
    InsufficientPrivilege,
)

from tests.conftest import _insert_test_user, _purge_user

_HASH_A = "a" * 64


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
    version_ids = [row["id"] for row in version_rows]

    for version_id in version_ids:
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
        # Clear live pointers before deleting versions.
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
    title: str = "Synthetic Retail Policy",
    lender_label: str = "synthetic_demo",
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
        (title, lender_label, created_by_user_id, now),
    )
    result = await row.fetchone()
    assert result is not None
    return result["id"]


async def _insert_policy_version(
    db_conn: AsyncConnection[Any],
    *,
    policy_document_id: UUID,
    created_by_user_id: UUID,
    version_label: str,
    status: str = "draft",
    effective_from: date,
    effective_to: date | None = None,
    annual_interest_rate_pct: Decimal = Decimal("10.50"),
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
            %s::policy_status,
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
            status,
            annual_interest_rate_pct,
            effective_from,
            effective_to,
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


@pytest.mark.asyncio
async def test_policy_document_version_thresholds_requirements_and_chunks(
    db_conn: AsyncConnection[Any],
) -> None:
    user = await _insert_test_user(
        db_conn,
        email="policy.schema.create@example.com",
        full_name="Policy Schema Create",
        role="admin",
        password="test-password-not-a-secret",
    )
    policy_document_id: UUID | None = None
    try:
        policy_document_id = await _insert_policy_document(
            db_conn,
            created_by_user_id=user["id"],
        )
        version_id = await _insert_policy_version(
            db_conn,
            policy_document_id=policy_document_id,
            created_by_user_id=user["id"],
            version_label="v1",
            status="draft",
            effective_from=date(2026, 1, 1),
            effective_to=date(2026, 12, 31),
        )
        now = datetime.now(timezone.utc)
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
                %s,
                'salaried'::applicant_type,
                %s, %s, 12, %s, %s
            )
            """,
            (
                version_id,
                Decimal("25000.00"),
                Decimal("50.00"),
                Decimal("2500000.00"),
                now,
            ),
        )
        await db_conn.execute(
            """
            INSERT INTO policy_document_requirements (
                policy_version_id,
                applicant_type,
                document_type,
                is_mandatory,
                created_at
            ) VALUES (
                %s,
                'salaried'::applicant_type,
                'salary_slip'::document_type,
                true,
                %s
            )
            """,
            (version_id, now),
        )
        await db_conn.execute(
            """
            INSERT INTO policy_chunks (
                policy_version_id,
                chunk_index,
                section_label,
                content_text,
                created_at
            ) VALUES (
                %s, 0, 'FOIR limits',
                'Maximum FOIR for salaried applicants is fifty percent.',
                %s
            )
            """,
            (version_id, now),
        )
        await db_conn.commit()

        row = await db_conn.execute(
            """
            SELECT pv.version_label,
                   pv.status::text AS status,
                   pt.max_foir_pct,
                   pr.document_type::text AS document_type,
                   pc.section_label,
                   pc.content_tsv IS NOT NULL AS has_tsv
              FROM policy_versions AS pv
              JOIN policy_threshold_configs AS pt
                ON pt.policy_version_id = pv.id
              JOIN policy_document_requirements AS pr
                ON pr.policy_version_id = pv.id
              JOIN policy_chunks AS pc
                ON pc.policy_version_id = pv.id
             WHERE pv.id = %s
            """,
            (version_id,),
        )
        created = await row.fetchone()
        assert created is not None
        assert created["version_label"] == "v1"
        assert created["status"] == "draft"
        assert created["max_foir_pct"] == Decimal("50.00")
        assert created["document_type"] == "salary_slip"
        assert created["section_label"] == "FOIR limits"
        assert created["has_tsv"] is True

        embedding_col = await db_conn.execute(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_schema = 'public'
               AND table_name = 'policy_chunks'
               AND column_name IN ('embedding', 'embedding_model')
            """
        )
        assert await embedding_col.fetchone() is None
    finally:
        if policy_document_id is not None:
            await _cleanup_policy_document(db_conn, policy_document_id)
        await _purge_user(db_conn, user["id"])


@pytest.mark.asyncio
async def test_active_policy_daterange_exclusion_blocks_overlap(
    db_conn: AsyncConnection[Any],
) -> None:
    user = await _insert_test_user(
        db_conn,
        email="policy.schema.overlap@example.com",
        full_name="Policy Schema Overlap",
        role="admin",
        password="test-password-not-a-secret",
    )
    doc_a: UUID | None = None
    doc_b: UUID | None = None
    try:
        doc_a = await _insert_policy_document(
            db_conn,
            created_by_user_id=user["id"],
            title="Policy A",
        )
        doc_b = await _insert_policy_document(
            db_conn,
            created_by_user_id=user["id"],
            title="Policy B",
        )
        await _insert_policy_version(
            db_conn,
            policy_document_id=doc_a,
            created_by_user_id=user["id"],
            version_label="active-a",
            status="active",
            effective_from=date(2026, 1, 1),
            effective_to=date(2026, 6, 30),
        )
        await db_conn.commit()

        excl = await db_conn.execute(
            """
            SELECT conname
              FROM pg_constraint
             WHERE conname = 'uq_policy_versions_active_no_overlap'
            """
        )
        assert await excl.fetchone() is not None

        # Overlap across distinct documents is still rejected globally.
        with pytest.raises(ExclusionViolation):
            await _insert_policy_version(
                db_conn,
                policy_document_id=doc_b,
                created_by_user_id=user["id"],
                version_label="active-b",
                status="active",
                effective_from=date(2026, 6, 1),
                effective_to=date(2026, 12, 31),
            )
            await db_conn.commit()
        await db_conn.rollback()
    finally:
        if doc_a is not None:
            await _cleanup_policy_document(db_conn, doc_a)
        if doc_b is not None:
            await _cleanup_policy_document(db_conn, doc_b)
        await _purge_user(db_conn, user["id"])


@pytest.mark.asyncio
async def test_distinct_non_overlapping_active_ranges_succeed(
    db_conn: AsyncConnection[Any],
) -> None:
    user = await _insert_test_user(
        db_conn,
        email="policy.schema.ranges@example.com",
        full_name="Policy Schema Ranges",
        role="admin",
        password="test-password-not-a-secret",
    )
    policy_document_id: UUID | None = None
    try:
        policy_document_id = await _insert_policy_document(
            db_conn,
            created_by_user_id=user["id"],
        )
        first = await _insert_policy_version(
            db_conn,
            policy_document_id=policy_document_id,
            created_by_user_id=user["id"],
            version_label="h1",
            status="active",
            effective_from=date(2026, 1, 1),
            effective_to=date(2026, 6, 30),
        )
        second = await _insert_policy_version(
            db_conn,
            policy_document_id=policy_document_id,
            created_by_user_id=user["id"],
            version_label="h2",
            status="active",
            effective_from=date(2026, 7, 1),
            effective_to=date(2026, 12, 31),
        )
        await db_conn.commit()
        assert first is not None
        assert second is not None

        count = await db_conn.execute(
            """
            SELECT COUNT(*)::int AS n
              FROM policy_versions
             WHERE policy_document_id = %s
               AND status = 'active'::policy_status
            """,
            (policy_document_id,),
        )
        n = await count.fetchone()
        assert n is not None
        assert n["n"] == 2
    finally:
        if policy_document_id is not None:
            await _cleanup_policy_document(db_conn, policy_document_id)
        await _purge_user(db_conn, user["id"])


@pytest.mark.asyncio
async def test_snapshot_insert_and_update_delete_blocked(
    db_conn: AsyncConnection[Any],
) -> None:
    user = await _insert_test_user(
        db_conn,
        email="policy.schema.snapshot@example.com",
        full_name="Policy Schema Snapshot",
        role="loan_officer",
        password="test-password-not-a-secret",
    )
    policy_document_id: UUID | None = None
    application_id: UUID | None = None
    try:
        policy_document_id = await _insert_policy_document(
            db_conn,
            created_by_user_id=user["id"],
        )
        version_id = await _insert_policy_version(
            db_conn,
            policy_document_id=policy_document_id,
            created_by_user_id=user["id"],
            version_label="snap-v1",
            status="active",
            effective_from=date(2026, 1, 1),
            effective_to=None,
        )
        application_id = await _insert_draft_application(
            db_conn,
            created_by_user_id=user["id"],
            reference_code=f"LL-SNAP-{uuid4().hex[:6].upper()}",
        )
        snapshot_id = await _insert_snapshot(
            db_conn,
            application_id=application_id,
            policy_version_id=version_id,
            created_by_user_id=user["id"],
        )
        await db_conn.commit()

        with pytest.raises(InsufficientPrivilege):
            await db_conn.execute(
                """
                UPDATE application_input_snapshots
                   SET loan_purpose = 'mutated'
                 WHERE id = %s
                """,
                (snapshot_id,),
            )
            await db_conn.commit()
        await db_conn.rollback()

        with pytest.raises(InsufficientPrivilege):
            await db_conn.execute(
                "DELETE FROM application_input_snapshots WHERE id = %s",
                (snapshot_id,),
            )
            await db_conn.commit()
        await db_conn.rollback()

        row = await db_conn.execute(
            """
            SELECT loan_purpose, snapshot_number
              FROM application_input_snapshots
             WHERE id = %s
            """,
            (snapshot_id,),
        )
        snap = await row.fetchone()
        assert snap is not None
        assert snap["loan_purpose"] == "home_renovation"
        assert snap["snapshot_number"] == 1
    finally:
        if application_id is not None:
            await _cleanup_application(db_conn, application_id)
        if policy_document_id is not None:
            await _cleanup_policy_document(db_conn, policy_document_id)
        await _purge_user(db_conn, user["id"])


@pytest.mark.asyncio
async def test_snapshot_calculation_inputs_composite_fk_integrity(
    db_conn: AsyncConnection[Any],
) -> None:
    user = await _insert_test_user(
        db_conn,
        email="policy.schema.calc@example.com",
        full_name="Policy Schema Calc",
        role="loan_officer",
        password="test-password-not-a-secret",
    )
    policy_document_id: UUID | None = None
    application_a: UUID | None = None
    application_b: UUID | None = None
    try:
        policy_document_id = await _insert_policy_document(
            db_conn,
            created_by_user_id=user["id"],
        )
        version_id = await _insert_policy_version(
            db_conn,
            policy_document_id=policy_document_id,
            created_by_user_id=user["id"],
            version_label="calc-v1",
            status="active",
            effective_from=date(2025, 1, 1),
            effective_to=date(2025, 12, 31),
        )
        application_a = await _insert_draft_application(
            db_conn,
            created_by_user_id=user["id"],
            reference_code=f"LL-CALCA-{uuid4().hex[:6].upper()}",
        )
        application_b = await _insert_draft_application(
            db_conn,
            created_by_user_id=user["id"],
            reference_code=f"LL-CALCB-{uuid4().hex[:6].upper()}",
        )
        snapshot_id = await _insert_snapshot(
            db_conn,
            application_id=application_a,
            policy_version_id=version_id,
            created_by_user_id=user["id"],
        )
        await db_conn.commit()

        now = datetime.now(timezone.utc)

        # Cross-application composite pair must fail (ADR-011).
        with pytest.raises(ForeignKeyViolation):
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
                    application_b,
                    snapshot_id,
                    Decimal("75000.00"),
                    Decimal("60000.00"),
                    Decimal("5000.00"),
                    Decimal("500000.00"),
                    Decimal("10.50"),
                    Decimal("50.00"),
                    now,
                ),
            )
            await db_conn.commit()
        await db_conn.rollback()

        ok = await db_conn.execute(
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
            RETURNING id
            """,
            (
                application_a,
                snapshot_id,
                Decimal("75000.00"),
                Decimal("60000.00"),
                Decimal("5000.00"),
                Decimal("500000.00"),
                Decimal("10.50"),
                Decimal("50.00"),
                now,
            ),
        )
        calc = await ok.fetchone()
        assert calc is not None
        await db_conn.commit()

        with pytest.raises(InsufficientPrivilege):
            await db_conn.execute(
                """
                UPDATE snapshot_calculation_inputs
                   SET monthly_obligations = %s
                 WHERE id = %s
                """,
                (Decimal("9999.00"), calc["id"]),
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
async def test_policy_chunks_content_tsv_fts_without_embeddings(
    db_conn: AsyncConnection[Any],
) -> None:
    user = await _insert_test_user(
        db_conn,
        email="policy.schema.fts@example.com",
        full_name="Policy Schema FTS",
        role="admin",
        password="test-password-not-a-secret",
    )
    policy_document_id: UUID | None = None
    try:
        policy_document_id = await _insert_policy_document(
            db_conn,
            created_by_user_id=user["id"],
        )
        version_id = await _insert_policy_version(
            db_conn,
            policy_document_id=policy_document_id,
            created_by_user_id=user["id"],
            version_label="fts-v1",
            status="draft",
            effective_from=date(2026, 3, 1),
        )
        now = datetime.now(timezone.utc)
        await db_conn.execute(
            """
            INSERT INTO policy_chunks (
                policy_version_id,
                chunk_index,
                section_label,
                content_text,
                created_at
            ) VALUES
                (%s, 0, 'Income',
                 'Minimum monthly income for salaried borrowers.', %s),
                (%s, 1, 'Obligations',
                 'Existing EMI obligations reduce disposable income.', %s)
            """,
            (version_id, now, version_id, now),
        )
        await db_conn.commit()

        gin = await db_conn.execute(
            """
            SELECT indexname
              FROM pg_indexes
             WHERE schemaname = 'public'
               AND indexname = 'policy_chunks_content_tsv_gin_idx'
            """
        )
        assert await gin.fetchone() is not None

        hit = await db_conn.execute(
            """
            SELECT section_label
              FROM policy_chunks
             WHERE policy_version_id = %s
               AND content_tsv @@ plainto_tsquery('english', 'salaried income')
            """,
            (version_id,),
        )
        matched = await hit.fetchone()
        assert matched is not None
        assert matched["section_label"] == "Income"

        miss = await db_conn.execute(
            """
            SELECT COUNT(*)::int AS n
              FROM policy_chunks
             WHERE policy_version_id = %s
               AND content_tsv @@ plainto_tsquery('english', 'vector embedding')
            """,
            (version_id,),
        )
        n = await miss.fetchone()
        assert n is not None
        assert n["n"] == 0

        cols = await db_conn.execute(
            """
            SELECT column_name, data_type
              FROM information_schema.columns
             WHERE table_schema = 'public'
               AND table_name = 'policy_chunks'
             ORDER BY ordinal_position
            """
        )
        column_names = {row["column_name"] for row in await cols.fetchall()}
        assert "content_tsv" in column_names
        assert "embedding" not in column_names
        assert "embedding_model" not in column_names
    finally:
        if policy_document_id is not None:
            await _cleanup_policy_document(db_conn, policy_document_id)
        await _purge_user(db_conn, user["id"])


@pytest.mark.asyncio
async def test_active_policy_freeze_allows_only_archive_transition(
    db_conn: AsyncConnection[Any],
) -> None:
    user = await _insert_test_user(
        db_conn,
        email="policy.schema.freeze@example.com",
        full_name="Policy Schema Freeze",
        role="admin",
        password="test-password-not-a-secret",
    )
    policy_document_id: UUID | None = None
    try:
        policy_document_id = await _insert_policy_document(
            db_conn,
            created_by_user_id=user["id"],
        )
        version_id = await _insert_policy_version(
            db_conn,
            policy_document_id=policy_document_id,
            created_by_user_id=user["id"],
            version_label="freeze-v1",
            status="active",
            effective_from=date(2024, 1, 1),
            effective_to=date(2024, 12, 31),
        )
        await db_conn.commit()

        with pytest.raises(CheckViolation, match="active policy_versions"):
            await db_conn.execute(
                """
                UPDATE policy_versions
                   SET version_label = 'mutated'
                 WHERE id = %s
                """,
                (version_id,),
            )
            await db_conn.commit()
        await db_conn.rollback()

        with pytest.raises(CheckViolation, match="cannot be deleted"):
            await db_conn.execute(
                "DELETE FROM policy_versions WHERE id = %s",
                (version_id,),
            )
            await db_conn.commit()
        await db_conn.rollback()

        now = datetime.now(timezone.utc)
        await db_conn.execute(
            """
            UPDATE policy_versions
               SET status = 'archived'::policy_status,
                   updated_at = %s
             WHERE id = %s
            """,
            (now, version_id),
        )
        await db_conn.commit()

        row = await db_conn.execute(
            """
            SELECT status::text AS status
              FROM policy_versions
             WHERE id = %s
            """,
            (version_id,),
        )
        archived = await row.fetchone()
        assert archived is not None
        assert archived["status"] == "archived"
    finally:
        await db_conn.rollback()
        if policy_document_id is not None:
            await _cleanup_policy_document(db_conn, policy_document_id)
        await _purge_user(db_conn, user["id"])
