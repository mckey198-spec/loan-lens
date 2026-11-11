"""Service and API tests for policy ingest, chunking, and selection (M06-T02)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from psycopg import AsyncConnection

from apps.api.main import app
from modules.policy.chunker import chunk_policy_markdown
from modules.policy.service import (
    activate_policy_version,
    create_policy_document,
    create_policy_version,
    ingest_policy_chunks,
    select_active_policy_version,
)
from tests.conftest import _insert_test_user, _purge_user
from tests.test_policies_and_snapshots_schema import _cleanup_policy_document

_SAMPLE_MARKDOWN = """# Eligibility

Salaried applicants must meet income thresholds.

## FOIR Limits

Maximum FOIR for salaried applicants is fifty percent.

### Document Checklist

Salary slips for the last three months are mandatory.
"""


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


def _threshold_payload() -> dict[str, Any]:
    return {
        "applicant_type": "salaried",
        "min_monthly_income": "25000.00",
        "max_foir_pct": "50.00",
        "min_employment_months": 12,
        "max_loan_amount": "2500000.00",
    }


def _requirement_payload() -> dict[str, Any]:
    return {
        "applicant_type": "salaried",
        "document_type": "salary_slip",
        "is_mandatory": True,
    }


@pytest.mark.asyncio
async def test_chunker_splits_markdown_and_preserves_labels() -> None:
    chunks = chunk_policy_markdown(_SAMPLE_MARKDOWN)
    assert [c.section_label for c in chunks] == [
        "Eligibility",
        "FOIR Limits",
        "Document Checklist",
    ]
    assert "income thresholds" in chunks[0].content_text
    assert "fifty percent" in chunks[1].content_text
    assert "three months" in chunks[2].content_text


@pytest.mark.asyncio
async def test_admin_create_version_ingest_activate_and_select(
    client: AsyncClient,
    db_conn: AsyncConnection[Any],
) -> None:
    admin = await _insert_test_user(
        db_conn,
        email="policy.svc.admin@example.com",
        full_name="Policy Service Admin",
        role="admin",
        password="admin-password-not-a-secret",
    )
    policy_document_id: UUID | None = None
    csrf = ""

    try:
        csrf = await _login(
            client,
            email=admin["email"],
            password=admin["password"],
        )

        create_doc = await client.post(
            "/api/v1/policies",
            headers={"X-CSRF-Token": csrf},
            json={
                "title": "Synthetic Retail Policy",
                "lender_label": "synthetic_demo",
            },
        )
        assert create_doc.status_code == 201, create_doc.text
        doc_body = create_doc.json()
        policy_document_id = UUID(doc_body["id"])
        assert doc_body["title"] == "Synthetic Retail Policy"

        create_version = await client.post(
            f"/api/v1/policies/{policy_document_id}/versions",
            headers={"X-CSRF-Token": csrf},
            json={
                "version_label": "v1-2026",
                "annual_interest_rate_pct": "10.50",
                "effective_from": "2026-01-01",
                "effective_to": "2026-12-31",
                "thresholds": [_threshold_payload()],
                "doc_requirements": [_requirement_payload()],
            },
        )
        assert create_version.status_code == 201, create_version.text
        version_body = create_version.json()
        version_id = UUID(version_body["id"])
        assert version_body["status"] == "draft"
        assert Decimal(version_body["annual_interest_rate_pct"]) == Decimal("10.50")

        ingest = await client.post(
            f"/api/v1/policies/versions/{version_id}/chunks",
            headers={"X-CSRF-Token": csrf},
            json={"markdown_text": _SAMPLE_MARKDOWN},
        )
        assert ingest.status_code == 201, ingest.text
        ingest_body = ingest.json()
        assert ingest_body["policy_version_id"] == str(version_id)
        assert len(ingest_body["chunks"]) == 3
        assert ingest_body["chunks"][0]["section_label"] == "Eligibility"
        assert ingest_body["chunks"][0]["chunk_index"] == 0
        assert ingest_body["chunks"][1]["chunk_index"] == 1
        assert ingest_body["chunks"][2]["section_label"] == "Document Checklist"

        chunk_row = await db_conn.execute(
            """
            SELECT content_tsv IS NOT NULL AS has_tsv
              FROM policy_chunks
             WHERE policy_version_id = %s
             ORDER BY chunk_index
             LIMIT 1
            """,
            (version_id,),
        )
        tsv = await chunk_row.fetchone()
        assert tsv is not None
        assert tsv["has_tsv"] is True

        activate = await client.post(
            f"/api/v1/policies/versions/{version_id}/activate",
            headers={"X-CSRF-Token": csrf},
        )
        assert activate.status_code == 200, activate.text
        assert activate.json()["status"] == "active"

        audit = await db_conn.execute(
            """
            SELECT action, entity_type, entity_id
              FROM audit_events
             WHERE action = 'policy.activated'
               AND entity_id = %s
            """,
            (version_id,),
        )
        audit_row = await audit.fetchone()
        assert audit_row is not None
        assert audit_row["entity_type"] == "policy_version"

        active = await client.get(
            "/api/v1/policies/active",
            params={"as_of_date": "2026-06-15"},
        )
        assert active.status_code == 200, active.text
        active_body = active.json()
        assert active_body["id"] == str(version_id)
        assert active_body["version_label"] == "v1-2026"
        assert len(active_body["thresholds"]) == 1
        assert active_body["thresholds"][0]["applicant_type"] == "salaried"
        assert Decimal(active_body["thresholds"][0]["max_foir_pct"]) == Decimal("50.00")
        assert len(active_body["doc_requirements"]) == 1
        assert active_body["doc_requirements"][0]["document_type"] == "salary_slip"

        miss = await client.get(
            "/api/v1/policies/active",
            params={"as_of_date": "2025-12-31"},
        )
        assert miss.status_code == 404
        assert miss.json()["code"] == "POLICY_NO_APPLICABLE"
    finally:
        if policy_document_id is not None:
            await _cleanup_policy_document(db_conn, policy_document_id)
        if csrf:
            await client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
        await _purge_user(db_conn, admin["id"])


@pytest.mark.asyncio
async def test_overlapping_activation_fails_with_conflict(
    client: AsyncClient,
    db_conn: AsyncConnection[Any],
) -> None:
    admin = await _insert_test_user(
        db_conn,
        email="policy.svc.overlap@example.com",
        full_name="Policy Overlap Admin",
        role="admin",
        password="admin-password-not-a-secret",
    )
    doc_a: UUID | None = None
    doc_b: UUID | None = None
    csrf = ""

    try:
        csrf = await _login(
            client,
            email=admin["email"],
            password=admin["password"],
        )

        first_doc = await client.post(
            "/api/v1/policies",
            headers={"X-CSRF-Token": csrf},
            json={"title": "Policy A", "lender_label": "synthetic_demo"},
        )
        assert first_doc.status_code == 201, first_doc.text
        doc_a = UUID(first_doc.json()["id"])

        first_version = await client.post(
            f"/api/v1/policies/{doc_a}/versions",
            headers={"X-CSRF-Token": csrf},
            json={
                "version_label": "a1",
                "annual_interest_rate_pct": "9.50",
                "effective_from": "2026-01-01",
                "effective_to": "2026-06-30",
                "thresholds": [_threshold_payload()],
                "doc_requirements": [],
            },
        )
        assert first_version.status_code == 201, first_version.text
        first_id = UUID(first_version.json()["id"])

        activate_first = await client.post(
            f"/api/v1/policies/versions/{first_id}/activate",
            headers={"X-CSRF-Token": csrf},
        )
        assert activate_first.status_code == 200, activate_first.text

        second_doc = await client.post(
            "/api/v1/policies",
            headers={"X-CSRF-Token": csrf},
            json={"title": "Policy B", "lender_label": "synthetic_demo"},
        )
        assert second_doc.status_code == 201, second_doc.text
        doc_b = UUID(second_doc.json()["id"])

        second_version = await client.post(
            f"/api/v1/policies/{doc_b}/versions",
            headers={"X-CSRF-Token": csrf},
            json={
                "version_label": "b1",
                "annual_interest_rate_pct": "11.00",
                "effective_from": "2026-06-01",
                "effective_to": "2026-12-31",
                "thresholds": [_threshold_payload()],
                "doc_requirements": [],
            },
        )
        assert second_version.status_code == 201, second_version.text
        second_id = UUID(second_version.json()["id"])

        activate_overlap = await client.post(
            f"/api/v1/policies/versions/{second_id}/activate",
            headers={"X-CSRF-Token": csrf},
        )
        assert activate_overlap.status_code == 409, activate_overlap.text
        assert activate_overlap.json()["code"] == "POLICY_ACTIVE_RANGE_CONFLICT"
    finally:
        if doc_a is not None:
            await _cleanup_policy_document(db_conn, doc_a)
        if doc_b is not None:
            await _cleanup_policy_document(db_conn, doc_b)
        if csrf:
            await client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
        await _purge_user(db_conn, admin["id"])


@pytest.mark.asyncio
async def test_select_active_policy_version_returns_thresholds(
    client: AsyncClient,
    db_conn: AsyncConnection[Any],
) -> None:
    admin = await _insert_test_user(
        db_conn,
        email="policy.svc.select@example.com",
        full_name="Policy Select Admin",
        role="admin",
        password="admin-password-not-a-secret",
    )
    pool = app.state.pool
    assert pool is not None
    policy_document_id: UUID | None = None

    try:
        document = await create_policy_document(
            pool,
            title="Select Test Policy",
            lender_label="synthetic_demo",
            created_by_user_id=admin["id"],
        )
        policy_document_id = document.id

        version = await create_policy_version(
            pool,
            policy_document_id=document.id,
            version_label="select-v1",
            annual_interest_rate_pct=Decimal("8.25"),
            effective_from=date(2026, 3, 1),
            effective_to=date(2026, 9, 30),
            thresholds=[_threshold_payload()],
            doc_requirements=[_requirement_payload()],
            created_by_user_id=admin["id"],
        )
        chunks = await ingest_policy_chunks(
            pool,
            policy_version_id=version.id,
            markdown_text=_SAMPLE_MARKDOWN,
        )
        assert len(chunks) == 3

        activated = await activate_policy_version(
            pool,
            policy_version_id=version.id,
            actor_user_id=admin["id"],
            request_id="test-select-policy",
        )
        assert activated.status == "active"

        selected = await select_active_policy_version(
            pool,
            as_of_date=date(2026, 5, 1),
        )
        assert selected is not None
        assert selected.id == version.id
        assert selected.annual_interest_rate_pct == Decimal("8.25")
        assert len(selected.thresholds) == 1
        assert selected.thresholds[0].min_monthly_income == Decimal("25000.00")
        assert selected.thresholds[0].max_foir_pct == Decimal("50.00")
        assert selected.doc_requirements[0].document_type == "salary_slip"

        outside = await select_active_policy_version(
            pool,
            as_of_date=date(2026, 10, 1),
        )
        assert outside is None
    finally:
        if policy_document_id is not None:
            await _cleanup_policy_document(db_conn, policy_document_id)
        await _purge_user(db_conn, admin["id"])


@pytest.mark.asyncio
async def test_non_admin_roles_forbidden_on_policy_mutations(
    client: AsyncClient,
    db_conn: AsyncConnection[Any],
) -> None:
    officer = await _insert_test_user(
        db_conn,
        email="policy.svc.officer@example.com",
        full_name="Policy Officer",
        role="loan_officer",
        password="officer-password-not-a-secret",
    )
    reviewer = await _insert_test_user(
        db_conn,
        email="policy.svc.reviewer@example.com",
        full_name="Policy Reviewer",
        role="reviewer",
        password="reviewer-password-not-a-secret",
    )
    csrf = ""

    try:
        for user in (officer, reviewer):
            csrf = await _login(
                client,
                email=user["email"],
                password=user["password"],
            )
            create_doc = await client.post(
                "/api/v1/policies",
                headers={"X-CSRF-Token": csrf},
                json={"title": "Denied", "lender_label": "synthetic_demo"},
            )
            assert create_doc.status_code == 403, create_doc.text
            assert create_doc.json()["code"] == "AUTH_FORBIDDEN"

            create_version = await client.post(
                "/api/v1/policies/00000000-0000-0000-0000-000000000001/versions",
                headers={"X-CSRF-Token": csrf},
                json={
                    "version_label": "denied",
                    "annual_interest_rate_pct": "10.00",
                    "effective_from": "2026-01-01",
                    "thresholds": [_threshold_payload()],
                    "doc_requirements": [],
                },
            )
            assert create_version.status_code == 403

            ingest = await client.post(
                "/api/v1/policies/versions/00000000-0000-0000-0000-000000000001/chunks",
                headers={"X-CSRF-Token": csrf},
                json={"markdown_text": "# A\n\nBody"},
            )
            assert ingest.status_code == 403

            activate = await client.post(
                "/api/v1/policies/versions/00000000-0000-0000-0000-000000000001/activate",
                headers={"X-CSRF-Token": csrf},
            )
            assert activate.status_code == 403

            await client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
            csrf = ""
    finally:
        if csrf:
            await client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
        await _purge_user(db_conn, officer["id"])
        await _purge_user(db_conn, reviewer["id"])
