"""Applications, applicants, and status-edge state machine.

Revision ID: 0003_applications_state
Revises: 0002_identity_and_audit
Create Date: 2026-11-05

Creates application_status (ten PRD §7 values only; ADR-017) and
applicant_type enums; applications; applicants; application_state_history;
submitted_at + status-edge triggers (TECHNICAL_ARCHITECTURE §5 /
Database Design §4.2).

applicable_policy_version_id is UUID NULL without FK until policy_versions
exists (M06). No annual_interest_rate_pct on applications (ADR-016).
No SQLAlchemy ORM models.

Note: revision id is shortened to fit alembic_version.version_num VARCHAR(32);
filename remains 0003_applications_and_state_machine.py (M03-T01).
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_applications_state"
down_revision: Union[str, Sequence[str], None] = "0002_identity_and_audit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TYPE application_status AS ENUM (
            'draft',
            'submitted',
            'processing',
            'processing_failed',
            'awaiting_human_review',
            'in_review',
            'clarification_required',
            'escalated',
            'review_completed',
            'archived'
        );
        """
    )
    op.execute(
        """
        CREATE TYPE applicant_type AS ENUM (
            'salaried',
            'self_employed'
        );
        """
    )

    op.execute(
        """
        CREATE TABLE applications (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            reference_code TEXT NOT NULL,
            status application_status NOT NULL,
            created_by_user_id UUID NOT NULL REFERENCES users (id),
            assigned_reviewer_user_id UUID NULL REFERENCES users (id),
            applicant_type applicant_type NOT NULL,
            requested_amount NUMERIC(18, 2) NOT NULL,
            currency CHAR(3) NOT NULL DEFAULT 'INR',
            tenure_months INTEGER NOT NULL,
            loan_purpose TEXT NOT NULL,
            loan_purpose_other TEXT NULL,
            applicable_policy_version_id UUID NULL,
            withdraw_reason TEXT NULL,
            submitted_at TIMESTAMPTZ NULL,
            archived_at TIMESTAMPTZ NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT applications_reference_code_key UNIQUE (reference_code),
            CONSTRAINT applications_requested_amount_positive_chk
                CHECK (requested_amount > 0),
            CONSTRAINT applications_tenure_months_positive_chk
                CHECK (tenure_months > 0),
            CONSTRAINT applications_loan_purpose_other_chk
                CHECK (
                    (loan_purpose <> 'other')
                    OR (
                        loan_purpose_other IS NOT NULL
                        AND length(trim(loan_purpose_other)) > 0
                    )
                ),
            CONSTRAINT applications_archived_at_chk
                CHECK ((status <> 'archived') OR (archived_at IS NOT NULL))
        );
        """
    )

    op.execute(
        """
        CREATE TABLE applicants (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            application_id UUID NOT NULL REFERENCES applications (id),
            full_name TEXT NOT NULL,
            date_of_birth DATE NOT NULL,
            pan CHAR(10) NOT NULL,
            current_address TEXT NOT NULL,
            employer_or_business_name TEXT NOT NULL,
            time_in_job_or_business_months INTEGER NOT NULL,
            gross_monthly_income NUMERIC(18, 2) NOT NULL,
            net_monthly_income NUMERIC(18, 2) NOT NULL,
            other_monthly_income NUMERIC(18, 2) NULL,
            other_monthly_income_evidenced BOOLEAN NOT NULL DEFAULT false,
            monthly_household_expenses NUMERIC(18, 2) NULL,
            total_existing_monthly_emis NUMERIC(18, 2) NOT NULL,
            outstanding_existing_loan_amount NUMERIC(18, 2) NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT applicants_application_id_key UNIQUE (application_id),
            CONSTRAINT applicants_time_in_job_nonneg_chk
                CHECK (time_in_job_or_business_months >= 0),
            CONSTRAINT applicants_gross_monthly_income_nonneg_chk
                CHECK (gross_monthly_income >= 0),
            CONSTRAINT applicants_net_monthly_income_nonneg_chk
                CHECK (net_monthly_income >= 0),
            CONSTRAINT applicants_net_le_gross_chk
                CHECK (net_monthly_income <= gross_monthly_income),
            CONSTRAINT applicants_total_existing_emis_nonneg_chk
                CHECK (total_existing_monthly_emis >= 0),
            CONSTRAINT applicants_other_monthly_income_nonneg_chk
                CHECK (
                    other_monthly_income IS NULL
                    OR other_monthly_income >= 0
                ),
            CONSTRAINT applicants_household_expenses_nonneg_chk
                CHECK (
                    monthly_household_expenses IS NULL
                    OR monthly_household_expenses >= 0
                ),
            CONSTRAINT applicants_outstanding_loan_nonneg_chk
                CHECK (
                    outstanding_existing_loan_amount IS NULL
                    OR outstanding_existing_loan_amount >= 0
                ),
            CONSTRAINT applicants_pan_format_chk
                CHECK (pan ~ '^[A-Z]{5}[0-9]{4}[A-Z]$')
        );
        """
    )

    op.execute(
        """
        CREATE TABLE application_state_history (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            application_id UUID NOT NULL REFERENCES applications (id),
            from_status application_status NULL,
            to_status application_status NOT NULL,
            actor_type audit_actor_type NOT NULL,
            actor_user_id UUID NULL REFERENCES users (id),
            reason TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL
        );
        """
    )
    op.execute(
        """
        CREATE INDEX application_state_history_application_id_created_at_idx
            ON application_state_history (application_id, created_at);
        """
    )

    # Append-only: block UPDATE/DELETE even for roles that retain table privileges.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION application_state_history_append_only()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
        BEGIN
            RAISE EXCEPTION
                'application_state_history is append-only'
                USING ERRCODE = '42501';
        END;
        $fn$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER application_state_history_append_only_trg
            BEFORE UPDATE OR DELETE ON application_state_history
            FOR EACH ROW
            EXECUTE FUNCTION application_state_history_append_only();
        """
    )
    op.execute(
        "REVOKE UPDATE, DELETE ON application_state_history FROM PUBLIC;"
    )

    # Status edges + submitted_at immutability / auto-set (BEFORE UPDATE).
    op.execute(
        """
        CREATE OR REPLACE FUNCTION applications_status_edge_before()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
        BEGIN
            IF OLD.submitted_at IS NOT NULL
               AND NEW.submitted_at IS DISTINCT FROM OLD.submitted_at THEN
                RAISE EXCEPTION
                    'submitted_at is immutable once set'
                    USING ERRCODE = '23514';
            END IF;

            IF OLD.status IS NOT DISTINCT FROM NEW.status THEN
                IF NEW.submitted_at IS DISTINCT FROM OLD.submitted_at THEN
                    RAISE EXCEPTION
                        'submitted_at may only be set when transitioning to submitted'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;

            IF NOT (
                (OLD.status = 'draft'
                    AND NEW.status IN ('submitted', 'archived'))
                OR (OLD.status = 'submitted'
                    AND NEW.status = 'processing')
                OR (OLD.status = 'processing'
                    AND NEW.status IN (
                        'awaiting_human_review',
                        'in_review',
                        'processing_failed'
                    ))
                OR (OLD.status = 'processing_failed'
                    AND NEW.status IN ('processing', 'archived'))
                OR (OLD.status = 'awaiting_human_review'
                    AND NEW.status IN ('in_review', 'archived'))
                OR (OLD.status = 'in_review'
                    AND NEW.status IN (
                        'clarification_required',
                        'escalated',
                        'review_completed',
                        'processing'
                    ))
                OR (OLD.status = 'clarification_required'
                    AND NEW.status IN ('processing', 'archived'))
                OR (OLD.status = 'escalated'
                    AND NEW.status IN ('in_review', 'review_completed'))
                OR (OLD.status = 'review_completed'
                    AND NEW.status IN ('processing', 'archived'))
            ) THEN
                RAISE EXCEPTION
                    'illegal application status transition: % -> %',
                    OLD.status,
                    NEW.status
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.status = 'submitted' AND NEW.submitted_at IS NULL THEN
                NEW.submitted_at := clock_timestamp();
            ELSIF NEW.submitted_at IS DISTINCT FROM OLD.submitted_at
                  AND NEW.status IS DISTINCT FROM 'submitted' THEN
                RAISE EXCEPTION
                    'submitted_at may only be set when transitioning to submitted'
                    USING ERRCODE = '23514';
            END IF;

            RETURN NEW;
        END;
        $fn$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER applications_status_edge_before_trg
            BEFORE UPDATE ON applications
            FOR EACH ROW
            EXECUTE FUNCTION applications_status_edge_before();
        """
    )

    # Same-transaction history row required for every status change (deferred).
    op.execute(
        """
        CREATE OR REPLACE FUNCTION applications_require_state_history()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
        BEGIN
            IF OLD.status IS NOT DISTINCT FROM NEW.status THEN
                RETURN NULL;
            END IF;

            IF NOT EXISTS (
                SELECT 1
                  FROM application_state_history AS h
                 WHERE h.application_id = NEW.id
                   AND h.from_status IS NOT DISTINCT FROM OLD.status
                   AND h.to_status = NEW.status
                   AND h.xmin = pg_current_xact_id()::xid
            ) THEN
                RAISE EXCEPTION
                    'application status change requires application_state_history '
                    'row for % -> % in the same transaction',
                    OLD.status,
                    NEW.status
                    USING ERRCODE = '23514';
            END IF;

            RETURN NULL;
        END;
        $fn$;
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER applications_require_state_history_trg
            AFTER UPDATE OF status ON applications
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW
            EXECUTE FUNCTION applications_require_state_history();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS applications_require_state_history_trg "
        "ON applications;"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS applications_require_state_history();"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS applications_status_edge_before_trg "
        "ON applications;"
    )
    op.execute("DROP FUNCTION IF EXISTS applications_status_edge_before();")
    op.execute(
        "DROP TRIGGER IF EXISTS application_state_history_append_only_trg "
        "ON application_state_history;"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS application_state_history_append_only();"
    )
    op.execute("DROP TABLE IF EXISTS application_state_history;")
    op.execute("DROP TABLE IF EXISTS applicants;")
    op.execute("DROP TABLE IF EXISTS applications;")
    op.execute("DROP TYPE IF EXISTS applicant_type;")
    op.execute("DROP TYPE IF EXISTS application_status;")
