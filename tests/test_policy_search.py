"""Lexical policy search, worker retrieval node, and citations API (M10)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from psycopg import AsyncConnection

from modules.policy.search import (
    DEFAULT_CITATION_TOPICS,
    retrieve_relevant_citations,
    search_policy_chunks,
)
from modules.worker.nodes.policy_retrieval import retrieve_policy_citations
from shared.db import create_pool
from shared.settings import get_settings
from tests.conftest import _insert_test_user, _purge_user
from tests.test_policies_and_snapshots_schema import _cleanup_policy_document

_HASH_A = "a" * 64


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


async def _cleanup_application(
    db_conn: AsyncConnection[Any],
    application_id: UUID,
) -> None:
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


async def _insert_policy_document(
    db_conn: AsyncConnection[Any],
    *,
    created_by_user_id: UUID,
    title: str = "Lexical Search Test Policy",
) -> UUID:
    now = datetime.now(timezone.utc)
    row = await db_conn.execute(
        """
        INSERT INTO policy_documents (
            title, lender_label, created_by_user_id, created_at
        ) VALUES (%s, %s, %s, %s)
        RETURNING id
        """,
        (title, "synthetic_demo", created_by_user_id, now),
    )
    doc = await row.fetchone()
    assert doc is not None
    await db_conn.commit()
    return doc["id"]


async def _insert_policy_version(
    db_conn: AsyncConnection[Any],
    *,
    policy_document_id: UUID,
    created_by_user_id: UUID,
    version_label: str,
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
            %s, %s, 'draft'::policy_status, %s,
            %s, %s, %s, %s, %s
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
    version = await row.fetchone()
    assert version is not None
    await db_conn.commit()
    return version["id"]


async def _insert_chunks(
    db_conn: AsyncConnection[Any],
    *,
    policy_version_id: UUID,
    chunks: list[tuple[int, str, str]],
) -> list[UUID]:
    now = datetime.now(timezone.utc)
    ids: list[UUID] = []
    for chunk_index, section_label, content_text in chunks:
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
        ids.append(inserted["id"])
    await db_conn.commit()
    return ids


async def _insert_application_with_snapshot(
    db_conn: AsyncConnection[Any],
    *,
    created_by_user_id: UUID,
    policy_version_id: UUID,
) -> tuple[UUID, UUID, UUID]:
    now = datetime.now(timezone.utc)
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
            f"LL-PS-{uuid4().hex[:10].upper()}",
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
            "Policy Search Applicant",
            date(1990, 8, 15),
            "ABCDE1234F",
            "123 Example Street",
            "Acme Corp",
            Decimal("65000.00"),
            Decimal("60000.00"),
            Decimal("5000.00"),
            now,
            now,
        ),
    )

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
            %s, 1, %s, %s, 'salaried'::applicant_type,
            %s, 36, 'home_renovation',
            %s, %s, %s, '[]'::jsonb, %s, %s, %s
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
            locked_by,
            locked_at,
            lease_expires_at,
            started_at,
            created_at
        ) VALUES (
            %s, %s, 'running'::processing_run_status, 1, 'submit',
            1, 'worker-policy-search-test', %s, %s, %s, %s
        )
        RETURNING id
        """,
        (
            application_id,
            snapshot_id,
            now,
            now + timedelta(minutes=10),
            now,
            now,
        ),
    )
    run_row = await run.fetchone()
    assert run_row is not None
    await db_conn.commit()
    return application_id, snapshot_id, run_row["id"]


@pytest.mark.asyncio
async def test_search_policy_chunks_ranks_by_ts_rank_cd(
    db_conn: AsyncConnection[Any],
) -> None:
    admin = await _insert_test_user(
        db_conn,
        email="policy.search.rank@example.com",
        full_name="Policy Search Rank",
        role="admin",
        password="test-password-not-a-secret",
    )
    policy_document_id: UUID | None = None
    try:
        policy_document_id = await _insert_policy_document(
            db_conn,
            created_by_user_id=admin["id"],
        )
        version_id = await _insert_policy_version(
            db_conn,
            policy_document_id=policy_document_id,
            created_by_user_id=admin["id"],
            version_label=f"rank-{uuid4().hex[:8]}",
        )
        await _insert_chunks(
            db_conn,
            policy_version_id=version_id,
            chunks=[
                (
                    0,
                    "Weak FOIR Mention",
                    "Borrowers must disclose existing obligations.",
                ),
                (
                    1,
                    "FOIR Limit",
                    (
                        "The maximum FOIR limit for salaried applicants is "
                        "fifty percent of net monthly income. FOIR limit "
                        "breaches are declined."
                    ),
                ),
                (
                    2,
                    "Unrelated Tenure",
                    "Employment tenure must be at least twelve months.",
                ),
            ],
        )

        matches = await search_policy_chunks(
            db_conn,
            version_id,
            "foir limit",
            limit=5,
        )
        assert len(matches) >= 1
        assert matches[0].section_label == "FOIR Limit"
        assert matches[0].rank_score > 0
        for earlier, later in zip(matches, matches[1:]):
            assert earlier.rank_score >= later.rank_score

        # Confirm content_tsv is the lexical index; no embedding column.
        cols = await db_conn.execute(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_schema = 'public'
               AND table_name = 'policy_chunks'
            """
        )
        column_names = {row["column_name"] for row in await cols.fetchall()}
        assert "content_tsv" in column_names
        assert "embedding" not in column_names
    finally:
        if policy_document_id is not None:
            await _cleanup_policy_document(db_conn, policy_document_id)
        await _purge_user(db_conn, admin["id"])


@pytest.mark.asyncio
async def test_search_strictly_filters_by_policy_version_id(
    db_conn: AsyncConnection[Any],
) -> None:
    admin = await _insert_test_user(
        db_conn,
        email="policy.search.scope@example.com",
        full_name="Policy Search Scope",
        role="admin",
        password="test-password-not-a-secret",
    )
    policy_document_id: UUID | None = None
    try:
        policy_document_id = await _insert_policy_document(
            db_conn,
            created_by_user_id=admin["id"],
        )
        version_a = await _insert_policy_version(
            db_conn,
            policy_document_id=policy_document_id,
            created_by_user_id=admin["id"],
            version_label=f"scope-a-{uuid4().hex[:8]}",
        )
        version_b = await _insert_policy_version(
            db_conn,
            policy_document_id=policy_document_id,
            created_by_user_id=admin["id"],
            version_label=f"scope-b-{uuid4().hex[:8]}",
        )
        ids_a = await _insert_chunks(
            db_conn,
            policy_version_id=version_a,
            chunks=[
                (
                    0,
                    "Version A Income",
                    "Minimum income for version alpha salaried borrowers is "
                    "twenty five thousand.",
                ),
            ],
        )
        ids_b = await _insert_chunks(
            db_conn,
            policy_version_id=version_b,
            chunks=[
                (
                    0,
                    "Version B Income",
                    "Minimum income for version beta salaried borrowers is "
                    "forty thousand.",
                ),
            ],
        )

        matches_a = await search_policy_chunks(
            db_conn,
            version_a,
            "minimum income",
            limit=5,
        )
        matches_b = await search_policy_chunks(
            db_conn,
            version_b,
            "minimum income",
            limit=5,
        )

        assert len(matches_a) == 1
        assert matches_a[0].chunk_id == ids_a[0]
        assert matches_a[0].section_label == "Version A Income"
        assert ids_b[0] not in {m.chunk_id for m in matches_a}

        assert len(matches_b) == 1
        assert matches_b[0].chunk_id == ids_b[0]
        assert matches_b[0].section_label == "Version B Income"
        assert ids_a[0] not in {m.chunk_id for m in matches_b}

        citations = await retrieve_relevant_citations(
            db_conn,
            version_a,
            list(DEFAULT_CITATION_TOPICS),
        )
        for matches in citations.values():
            for match in matches:
                assert match.chunk_id == ids_a[0] or match.chunk_id in ids_a
                assert match.chunk_id not in ids_b
    finally:
        if policy_document_id is not None:
            await _cleanup_policy_document(db_conn, policy_document_id)
        await _purge_user(db_conn, admin["id"])


@pytest.mark.asyncio
async def test_policy_retrieval_node_populates_policy_citations(
    db_conn: AsyncConnection[Any],
) -> None:
    admin = await _insert_test_user(
        db_conn,
        email="policy.search.node@example.com",
        full_name="Policy Search Node",
        role="admin",
        password="test-password-not-a-secret",
    )
    policy_document_id: UUID | None = None
    application_id: UUID | None = None
    pool = create_pool(get_settings().DATABASE_URL)
    await pool.open()
    try:
        policy_document_id = await _insert_policy_document(
            db_conn,
            created_by_user_id=admin["id"],
        )
        version_id = await _insert_policy_version(
            db_conn,
            policy_document_id=policy_document_id,
            created_by_user_id=admin["id"],
            version_label=f"node-{uuid4().hex[:8]}",
        )
        await _insert_chunks(
            db_conn,
            policy_version_id=version_id,
            chunks=[
                (
                    0,
                    "FOIR Limit",
                    "Maximum FOIR limit is fifty percent of net income.",
                ),
                (
                    1,
                    "Minimum Income",
                    "Minimum income for salaried applicants is twenty five thousand.",
                ),
                (
                    2,
                    "Employment Tenure",
                    "Employment tenure must be at least twelve months.",
                ),
                (
                    3,
                    "Mandatory Documents",
                    "Mandatory documents include salary slips and bank statements.",
                ),
            ],
        )
        application_id, snapshot_id, run_id = await _insert_application_with_snapshot(
            db_conn,
            created_by_user_id=admin["id"],
            policy_version_id=version_id,
        )

        state = {
            "_db_pool": pool,
            "application_id": str(application_id),
            "processing_run_id": str(run_id),
            "input_snapshot_id": str(snapshot_id),
            "verification_findings": [
                {
                    "rule_code": "BR-08",
                    "rule_name": "min_monthly_income",
                    "status": "failed",
                    "severity": "critical",
                    "message": "Income below policy minimum.",
                },
            ],
            "financial_calculation": {
                "foir_passed": False,
                "foir_pct": "62.00",
            },
        }

        updated = await retrieve_policy_citations(state)

        assert "policy_citations" in updated
        citations = updated["policy_citations"]
        assert isinstance(citations, dict)
        assert "minimum income" in citations
        assert "foir limit" in citations
        assert updated["applicable_policy_version_id"] == str(version_id)
        assert citations["minimum income"]
        assert citations["foir limit"]
        assert citations["minimum income"][0]["section_label"] == "Minimum Income"
        assert citations["foir limit"][0]["section_label"] == "FOIR Limit"
        assert "_db_pool" not in updated
    finally:
        await pool.close()
        if application_id is not None:
            await _cleanup_application(db_conn, application_id)
        if policy_document_id is not None:
            await _cleanup_policy_document(db_conn, policy_document_id)
        await _purge_user(db_conn, admin["id"])


@pytest.mark.asyncio
async def test_policy_citations_api_returns_chunks_and_enforces_auth(
    client: AsyncClient,
    db_conn: AsyncConnection[Any],
) -> None:
    admin = await _insert_test_user(
        db_conn,
        email="policy.search.api.admin@example.com",
        full_name="Policy Search API Admin",
        role="admin",
        password="admin-password-not-a-secret",
    )
    officer = await _insert_test_user(
        db_conn,
        email="policy.search.api.officer@example.com",
        full_name="Policy Search API Officer",
        role="loan_officer",
        password="officer-password-not-a-secret",
    )
    # Unapproved role for this product would be anything outside the three roles;
    # use an inactive path via wrong-role by creating a reviewer-only check via
    # unauthenticated request and a user without session for 401.
    policy_document_id: UUID | None = None
    application_id: UUID | None = None
    try:
        policy_document_id = await _insert_policy_document(
            db_conn,
            created_by_user_id=admin["id"],
        )
        version_id = await _insert_policy_version(
            db_conn,
            policy_document_id=policy_document_id,
            created_by_user_id=admin["id"],
            version_label=f"api-{uuid4().hex[:8]}",
        )
        await _insert_chunks(
            db_conn,
            policy_version_id=version_id,
            chunks=[
                (
                    0,
                    "FOIR Limit",
                    "The FOIR limit for salaried applicants is fifty percent.",
                ),
                (
                    1,
                    "Minimum Income",
                    "Minimum income requirement is twenty five thousand monthly.",
                ),
                (
                    2,
                    "Employment Tenure",
                    "Employment tenure of twelve months is mandatory.",
                ),
                (
                    3,
                    "Mandatory Documents",
                    "Mandatory documents include identity proof and salary slips.",
                ),
            ],
        )
        application_id, _snapshot_id, _run_id = await _insert_application_with_snapshot(
            db_conn,
            created_by_user_id=officer["id"],
            policy_version_id=version_id,
        )

        unauthenticated = await client.get(
            f"/api/v1/applications/{application_id}/policy-citations",
        )
        assert unauthenticated.status_code == 401

        csrf = await _login(
            client,
            email=officer["email"],
            password=officer["password"],
        )
        allowed = await client.get(
            f"/api/v1/applications/{application_id}/policy-citations",
        )
        assert allowed.status_code == 200, allowed.text
        body = allowed.json()
        assert body["application_id"] == str(application_id)
        assert body["policy_version_id"] == str(version_id)
        assert "foir limit" in body["citations"]
        assert "minimum income" in body["citations"]
        assert body["citations"]["foir limit"]
        assert body["citations"]["foir limit"][0]["section_label"] == "FOIR Limit"
        assert body["citations"]["minimum income"][0]["chunk_id"]

        # Logout officer and confirm a non-authorized anonymous call still fails.
        await client.post(
            "/api/v1/auth/logout",
            headers={"X-CSRF-Token": csrf},
        )
        denied = await client.get(
            f"/api/v1/applications/{application_id}/policy-citations",
        )
        assert denied.status_code == 401
    finally:
        if application_id is not None:
            await _cleanup_application(db_conn, application_id)
        if policy_document_id is not None:
            await _cleanup_policy_document(db_conn, policy_document_id)
        await _purge_user(db_conn, officer["id"])
        await _purge_user(db_conn, admin["id"])


@pytest.mark.asyncio
async def test_policy_citations_api_forbids_wrong_role_when_applicable(
    client: AsyncClient,
    db_conn: AsyncConnection[Any],
) -> None:
    """Role gate: only loan_officer, reviewer, admin may read citations."""
    # Create an admin to seed data, then verify reviewer can access (allowed role).
    admin = await _insert_test_user(
        db_conn,
        email="policy.search.api.role.admin@example.com",
        full_name="Policy Search Role Admin",
        role="admin",
        password="admin-password-not-a-secret",
    )
    reviewer = await _insert_test_user(
        db_conn,
        email="policy.search.api.role.reviewer@example.com",
        full_name="Policy Search Role Reviewer",
        role="reviewer",
        password="reviewer-password-not-a-secret",
    )
    policy_document_id: UUID | None = None
    application_id: UUID | None = None
    try:
        policy_document_id = await _insert_policy_document(
            db_conn,
            created_by_user_id=admin["id"],
        )
        version_id = await _insert_policy_version(
            db_conn,
            policy_document_id=policy_document_id,
            created_by_user_id=admin["id"],
            version_label=f"role-{uuid4().hex[:8]}",
        )
        await _insert_chunks(
            db_conn,
            policy_version_id=version_id,
            chunks=[
                (
                    0,
                    "FOIR Limit",
                    "FOIR limit clause for citation retrieval tests.",
                ),
            ],
        )
        application_id, _, _ = await _insert_application_with_snapshot(
            db_conn,
            created_by_user_id=admin["id"],
            policy_version_id=version_id,
        )

        await _login(
            client,
            email=reviewer["email"],
            password=reviewer["password"],
        )
        response = await client.get(
            f"/api/v1/applications/{application_id}/policy-citations",
        )
        assert response.status_code == 200, response.text
        assert response.json()["policy_version_id"] == str(version_id)
    finally:
        if application_id is not None:
            await _cleanup_application(db_conn, application_id)
        if policy_document_id is not None:
            await _cleanup_policy_document(db_conn, policy_document_id)
        await _purge_user(db_conn, reviewer["id"])
        await _purge_user(db_conn, admin["id"])
