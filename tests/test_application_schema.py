"""Schema tests for applications, applicants, and status-edge triggers (M03-T01)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from psycopg import AsyncConnection
from psycopg.errors import CheckViolation

from tests.conftest import _insert_test_user, _purge_user

_TEN_APPLICATION_STATUSES = (
    "draft",
    "submitted",
    "processing",
    "processing_failed",
    "awaiting_human_review",
    "in_review",
    "clarification_required",
    "escalated",
    "review_completed",
    "archived",
)


async def _cleanup_application(
    db_conn: AsyncConnection[Any],
    application_id: UUID,
) -> None:
    # Append-only trigger blocks DELETE; disable only for test teardown.
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


async def _insert_applicant(
    db_conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    pan: str = "ABCDE1234F",
    gross_monthly_income: Decimal = Decimal("100000.00"),
    net_monthly_income: Decimal = Decimal("80000.00"),
) -> UUID:
    now = datetime.now(timezone.utc)
    row = await db_conn.execute(
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
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        RETURNING id
        """,
        (
            application_id,
            "Test Applicant",
            date(1990, 1, 15),
            pan,
            "123 Test Street, Bhubaneswar",
            "Example Employer Pvt Ltd",
            24,
            gross_monthly_income,
            net_monthly_income,
            Decimal("0.00"),
            now,
            now,
        ),
    )
    result = await row.fetchone()
    assert result is not None
    return result["id"]


async def _transition(
    db_conn: AsyncConnection[Any],
    *,
    application_id: UUID,
    from_status: str,
    to_status: str,
    actor_user_id: UUID,
    reason: str | None = None,
    set_archived_at: bool = False,
) -> None:
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
            %s::application_status,
            %s::application_status,
            'user'::audit_actor_type,
            %s,
            %s,
            %s
        )
        """,
        (application_id, from_status, to_status, actor_user_id, reason, now),
    )
    if set_archived_at or to_status == "archived":
        await db_conn.execute(
            """
            UPDATE applications
               SET status = %s::application_status,
                   archived_at = COALESCE(archived_at, %s),
                   updated_at = %s
             WHERE id = %s
            """,
            (to_status, now, now, application_id),
        )
    else:
        await db_conn.execute(
            """
            UPDATE applications
               SET status = %s::application_status,
                   updated_at = %s
             WHERE id = %s
            """,
            (to_status, now, application_id),
        )


@pytest.mark.asyncio
async def test_application_status_enum_has_exactly_ten_prd_states(
    db_conn: AsyncConnection[Any],
) -> None:
    rows = await db_conn.execute(
        """
        SELECT enumlabel
          FROM pg_enum e
          JOIN pg_type t ON t.oid = e.enumtypid
         WHERE t.typname = 'application_status'
         ORDER BY e.enumsortorder
        """
    )
    labels = [row["enumlabel"] for row in await rows.fetchall()]
    assert labels == list(_TEN_APPLICATION_STATUSES)
    assert "needs_documents" not in labels
    assert len(labels) == 10


@pytest.mark.asyncio
async def test_create_draft_application_and_applicant(
    db_conn: AsyncConnection[Any],
) -> None:
    user = await _insert_test_user(
        db_conn,
        email="schema.officer@example.com",
        full_name="Schema Officer",
        role="loan_officer",
        password="test-password-not-a-secret",
    )
    application_id: UUID | None = None
    try:
        application_id = await _insert_draft_application(
            db_conn,
            created_by_user_id=user["id"],
        )
        applicant_id = await _insert_applicant(
            db_conn,
            application_id=application_id,
        )
        await db_conn.commit()

        app_row = await db_conn.execute(
            """
            SELECT status::text AS status,
                   currency,
                   requested_amount,
                   submitted_at,
                   applicable_policy_version_id
              FROM applications
             WHERE id = %s
            """,
            (application_id,),
        )
        application = await app_row.fetchone()
        assert application is not None
        assert application["status"] == "draft"
        assert application["currency"] == "INR"
        assert application["requested_amount"] == Decimal("500000.00")
        assert application["submitted_at"] is None
        assert application["applicable_policy_version_id"] is None

        cols = await db_conn.execute(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_schema = 'public'
               AND table_name = 'applications'
               AND column_name = 'annual_interest_rate_pct'
            """
        )
        assert await cols.fetchone() is None

        applicant_row = await db_conn.execute(
            """
            SELECT id, pan, net_monthly_income, gross_monthly_income
              FROM applicants
             WHERE id = %s
            """,
            (applicant_id,),
        )
        applicant = await applicant_row.fetchone()
        assert applicant is not None
        assert applicant["pan"] == "ABCDE1234F"
        assert applicant["net_monthly_income"] <= applicant["gross_monthly_income"]
    finally:
        if application_id is not None:
            await _cleanup_application(db_conn, application_id)
        await _purge_user(db_conn, user["id"])


@pytest.mark.asyncio
async def test_trigger_blocks_illegal_status_transitions(
    db_conn: AsyncConnection[Any],
) -> None:
    user = await _insert_test_user(
        db_conn,
        email="schema.edges@example.com",
        full_name="Schema Edges",
        role="loan_officer",
        password="test-password-not-a-secret",
    )
    application_id: UUID | None = None
    try:
        application_id = await _insert_draft_application(
            db_conn,
            created_by_user_id=user["id"],
            reference_code=f"LL-EDGE-{uuid4().hex[:8].upper()}",
        )
        await db_conn.commit()

        with pytest.raises(CheckViolation):
            await _transition(
                db_conn,
                application_id=application_id,
                from_status="draft",
                to_status="review_completed",
                actor_user_id=user["id"],
            )
            await db_conn.commit()
        await db_conn.rollback()

        await _transition(
            db_conn,
            application_id=application_id,
            from_status="draft",
            to_status="archived",
            actor_user_id=user["id"],
            reason="withdrawn",
            set_archived_at=True,
        )
        await db_conn.commit()

        with pytest.raises(CheckViolation):
            await _transition(
                db_conn,
                application_id=application_id,
                from_status="archived",
                to_status="draft",
                actor_user_id=user["id"],
            )
            await db_conn.commit()
        await db_conn.rollback()
    finally:
        if application_id is not None:
            await _cleanup_application(db_conn, application_id)
        await _purge_user(db_conn, user["id"])


@pytest.mark.asyncio
async def test_trigger_blocks_status_change_without_history_row(
    db_conn: AsyncConnection[Any],
) -> None:
    user = await _insert_test_user(
        db_conn,
        email="schema.history@example.com",
        full_name="Schema History",
        role="loan_officer",
        password="test-password-not-a-secret",
    )
    application_id: UUID | None = None
    try:
        application_id = await _insert_draft_application(
            db_conn,
            created_by_user_id=user["id"],
            reference_code=f"LL-HIST-{uuid4().hex[:8].upper()}",
        )
        await db_conn.commit()

        now = datetime.now(timezone.utc)
        with pytest.raises(CheckViolation):
            await db_conn.execute(
                """
                UPDATE applications
                   SET status = 'submitted'::application_status,
                       updated_at = %s
                 WHERE id = %s
                """,
                (now, application_id),
            )
            await db_conn.commit()
        await db_conn.rollback()

        # Legal edge with matching history in the same transaction succeeds.
        await _transition(
            db_conn,
            application_id=application_id,
            from_status="draft",
            to_status="submitted",
            actor_user_id=user["id"],
        )
        await db_conn.commit()

        row = await db_conn.execute(
            """
            SELECT status::text AS status, submitted_at
              FROM applications
             WHERE id = %s
            """,
            (application_id,),
        )
        application = await row.fetchone()
        assert application is not None
        assert application["status"] == "submitted"
        assert application["submitted_at"] is not None
    finally:
        if application_id is not None:
            await _cleanup_application(db_conn, application_id)
        await _purge_user(db_conn, user["id"])


@pytest.mark.asyncio
async def test_applicant_constraints_reject_invalid_pan_and_net_gt_gross(
    db_conn: AsyncConnection[Any],
) -> None:
    user = await _insert_test_user(
        db_conn,
        email="schema.constraints@example.com",
        full_name="Schema Constraints",
        role="loan_officer",
        password="test-password-not-a-secret",
    )
    application_id: UUID | None = None
    try:
        application_id = await _insert_draft_application(
            db_conn,
            created_by_user_id=user["id"],
            reference_code=f"LL-CHK-{uuid4().hex[:8].upper()}",
        )
        await db_conn.commit()

        with pytest.raises(CheckViolation):
            await _insert_applicant(
                db_conn,
                application_id=application_id,
                pan="INVALIDPAN",
            )
            await db_conn.commit()
        await db_conn.rollback()

        with pytest.raises(CheckViolation):
            await _insert_applicant(
                db_conn,
                application_id=application_id,
                pan="ABCDE1234F",
                gross_monthly_income=Decimal("50000.00"),
                net_monthly_income=Decimal("60000.00"),
            )
            await db_conn.commit()
        await db_conn.rollback()
    finally:
        if application_id is not None:
            await _cleanup_application(db_conn, application_id)
        await _purge_user(db_conn, user["id"])
