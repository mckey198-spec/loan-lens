"""API tests for draft application intake (M03-T02)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from psycopg import AsyncConnection

from apps.api.main import app


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


def _valid_create_payload(*, omit_optional_money: bool = True) -> dict[str, Any]:
    applicant: dict[str, Any] = {
        "full_name": "Synthetic Applicant",
        "date_of_birth": "1990-01-15",
        "pan": "ABCDE1234F",
        "current_address": "12 Example Road, Bhubaneswar",
        "employer_or_business_name": "Example Employer Pvt Ltd",
        "time_in_job_or_business_months": 24,
        "gross_monthly_income": "100000.00",
        "net_monthly_income": "80000.00",
        "total_existing_monthly_emis": "5000.00",
    }
    if not omit_optional_money:
        applicant["other_monthly_income"] = "1000.00"
        applicant["monthly_household_expenses"] = "15000.00"
        applicant["outstanding_existing_loan_amount"] = "25000.00"

    return {
        "applicant_type": "salaried",
        "requested_amount": "500000.00",
        "tenure_months": 36,
        "loan_purpose": "home_renovation",
        "applicant": applicant,
    }


@pytest.mark.asyncio
async def test_loan_officer_creates_draft_with_history(
    client: AsyncClient,
    test_user: dict[str, Any],
    db_conn: AsyncConnection[Any],
) -> None:
    csrf = await _login(
        client,
        email=test_user["email"],
        password=test_user["password"],
    )
    application_id: UUID | None = None
    try:
        response = await client.post(
            "/api/v1/applications",
            headers={"X-CSRF-Token": csrf},
            json=_valid_create_payload(),
        )
        assert response.status_code == 201, response.text
        body = response.json()
        application_id = UUID(body["id"])

        assert body["status"] == "draft"
        assert body["reference_code"].startswith("LL-")
        assert body["created_by_user_id"] == str(test_user["id"])
        assert body["applicant"]["full_name"] == "Synthetic Applicant"
        assert body["applicant"]["other_monthly_income"] is None
        assert body["applicant"]["monthly_household_expenses"] is None
        assert body["applicant"]["outstanding_existing_loan_amount"] is None

        history = await db_conn.execute(
            """
            SELECT from_status::text AS from_status,
                   to_status::text AS to_status,
                   actor_type::text AS actor_type,
                   actor_user_id
            FROM application_state_history
            WHERE application_id = %s
            """,
            (application_id,),
        )
        rows = await history.fetchall()
        assert len(rows) == 1
        assert rows[0]["from_status"] is None
        assert rows[0]["to_status"] == "draft"
        assert rows[0]["actor_type"] == "user"
        assert rows[0]["actor_user_id"] == test_user["id"]

        amounts = await db_conn.execute(
            """
            SELECT other_monthly_income,
                   monthly_household_expenses,
                   outstanding_existing_loan_amount
            FROM applicants
            WHERE application_id = %s
            """,
            (application_id,),
        )
        money = await amounts.fetchone()
        assert money is not None
        assert money["other_monthly_income"] is None
        assert money["monthly_household_expenses"] is None
        assert money["outstanding_existing_loan_amount"] is None
    finally:
        if application_id is not None:
            await _cleanup_application(db_conn, application_id)


@pytest.mark.asyncio
async def test_reviewer_forbidden_from_create_and_update(
    client: AsyncClient,
    reviewer_user: dict[str, Any],
    test_user: dict[str, Any],
    db_conn: AsyncConnection[Any],
) -> None:
    officer_client = AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    )
    application_id: UUID | None = None
    try:
        async with officer_client:
            officer_csrf = await _login(
                officer_client,
                email=test_user["email"],
                password=test_user["password"],
            )
            created = await officer_client.post(
                "/api/v1/applications",
                headers={"X-CSRF-Token": officer_csrf},
                json=_valid_create_payload(),
            )
            assert created.status_code == 201, created.text
            application_id = UUID(created.json()["id"])

        reviewer_csrf = await _login(
            client,
            email=reviewer_user["email"],
            password=reviewer_user["password"],
        )
        create = await client.post(
            "/api/v1/applications",
            headers={"X-CSRF-Token": reviewer_csrf},
            json=_valid_create_payload(),
        )
        assert create.status_code == 403
        assert create.json()["code"] == "AUTH_FORBIDDEN"

        update = await client.patch(
            f"/api/v1/applications/{application_id}",
            headers={"X-CSRF-Token": reviewer_csrf},
            json={"tenure_months": 48},
        )
        assert update.status_code == 403
        assert update.json()["code"] == "AUTH_FORBIDDEN"
    finally:
        if application_id is not None:
            await _cleanup_application(db_conn, application_id)


@pytest.mark.asyncio
async def test_validation_rejects_net_gt_gross_and_invalid_pan(
    client: AsyncClient,
    test_user: dict[str, Any],
) -> None:
    csrf = await _login(
        client,
        email=test_user["email"],
        password=test_user["password"],
    )

    net_gt_gross = _valid_create_payload()
    net_gt_gross["applicant"]["gross_monthly_income"] = "50000.00"
    net_gt_gross["applicant"]["net_monthly_income"] = "60000.00"
    response = await client.post(
        "/api/v1/applications",
        headers={"X-CSRF-Token": csrf},
        json=net_gt_gross,
    )
    assert response.status_code == 422

    invalid_pan = _valid_create_payload()
    invalid_pan["applicant"]["pan"] = "INVALIDPAN"
    response = await client.post(
        "/api/v1/applications",
        headers={"X-CSRF-Token": csrf},
        json=invalid_pan,
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_draft_update_succeeds(
    client: AsyncClient,
    test_user: dict[str, Any],
    db_conn: AsyncConnection[Any],
) -> None:
    csrf = await _login(
        client,
        email=test_user["email"],
        password=test_user["password"],
    )
    application_id: UUID | None = None
    try:
        created = await client.post(
            "/api/v1/applications",
            headers={"X-CSRF-Token": csrf},
            json=_valid_create_payload(),
        )
        assert created.status_code == 201, created.text
        application_id = UUID(created.json()["id"])

        updated = await client.patch(
            f"/api/v1/applications/{application_id}",
            headers={"X-CSRF-Token": csrf},
            json={
                "requested_amount": "750000.00",
                "tenure_months": 48,
                "applicant": {
                    "employer_or_business_name": "Updated Employer Ltd",
                    "net_monthly_income": "78000.00",
                },
            },
        )
        assert updated.status_code == 200, updated.text
        body = updated.json()
        assert body["status"] == "draft"
        assert Decimal(body["requested_amount"]) == Decimal("750000.00")
        assert body["tenure_months"] == 48
        assert body["applicant"]["employer_or_business_name"] == "Updated Employer Ltd"
        assert Decimal(body["applicant"]["net_monthly_income"]) == Decimal("78000.00")
        # Optional money fields remain null after update that did not set them.
        assert body["applicant"]["other_monthly_income"] is None
        assert body["applicant"]["monthly_household_expenses"] is None
        assert body["applicant"]["outstanding_existing_loan_amount"] is None

        detail = await client.get(f"/api/v1/applications/{application_id}")
        assert detail.status_code == 200
        assert detail.json()["reference_code"] == body["reference_code"]

        listing = await client.get(
            "/api/v1/applications",
            params={"status": "draft", "offset": 0, "limit": 50},
        )
        assert listing.status_code == 200
        list_body = listing.json()
        assert list_body["total"] >= 1
        assert any(item["id"] == str(application_id) for item in list_body["items"])
    finally:
        if application_id is not None:
            await _cleanup_application(db_conn, application_id)


@pytest.mark.asyncio
async def test_admin_can_create_draft(
    client: AsyncClient,
    admin_user: dict[str, Any],
    db_conn: AsyncConnection[Any],
) -> None:
    csrf = await _login(
        client,
        email=admin_user["email"],
        password=admin_user["password"],
    )
    application_id: UUID | None = None
    try:
        response = await client.post(
            "/api/v1/applications",
            headers={"X-CSRF-Token": csrf},
            json=_valid_create_payload(omit_optional_money=False),
        )
        assert response.status_code == 201, response.text
        body = response.json()
        application_id = UUID(body["id"])
        assert body["status"] == "draft"
        assert Decimal(body["applicant"]["other_monthly_income"]) == Decimal("1000.00")
    finally:
        if application_id is not None:
            await _cleanup_application(db_conn, application_id)
