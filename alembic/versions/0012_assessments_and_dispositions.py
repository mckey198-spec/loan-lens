"""Assessments, finding dispositions, and field corrections (M12-T01).

Revision ID: 0012_assessments_dispositions
Revises: 0011_ai_observations
Create Date: 2026-11-05

Creates assessment_recommendation and finding_disposition_action enums;
assessments (one per processing run, composite FKs to processing_runs and
application_input_snapshots — ADR-011); finding_dispositions (append-only
reviewer actions on verification_findings); field_corrections (typed
corrected values deferred from M07 per ADR-025 / Database Design §4.7).

finding_dispositions and field_corrections revoke UPDATE/DELETE from PUBLIC
and block mutation via append-only triggers. Assessments freeze once
decided_at IS NOT NULL (no in-place rewrite of a decided package).

No SQLAlchemy ORM models.

Note: revision id fits alembic_version.version_num VARCHAR(32);
filename remains 0012_assessments_and_dispositions.py.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012_assessments_dispositions"
down_revision: Union[str, Sequence[str], None] = "0011_ai_observations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TYPE assessment_recommendation AS ENUM (
            'approve',
            'reject',
            'refer',
            'exception_approved'
        );
        """
    )
    op.execute(
        """
        CREATE TYPE finding_disposition_action AS ENUM (
            'accepted_risk',
            'overridden',
            'confirmed_issue',
            'false_positive'
        );
        """
    )

    op.execute(
        """
        CREATE TABLE assessments (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            application_id UUID NOT NULL,
            processing_run_id UUID NOT NULL,
            input_snapshot_id UUID NOT NULL,
            applicable_policy_version_id UUID NOT NULL,
            assigned_reviewer_id UUID NULL,
            recommendation assessment_recommendation NULL,
            reviewer_notes TEXT NULL,
            decided_at TIMESTAMPTZ NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT fk_assessments_app
                FOREIGN KEY (application_id)
                REFERENCES applications (id),
            CONSTRAINT fk_assessments_proc
                FOREIGN KEY (processing_run_id, application_id)
                REFERENCES processing_runs (id, application_id),
            CONSTRAINT fk_assessments_snap
                FOREIGN KEY (input_snapshot_id, application_id)
                REFERENCES application_input_snapshots (id, application_id),
            CONSTRAINT fk_assessments_policy
                FOREIGN KEY (applicable_policy_version_id)
                REFERENCES policy_versions (id),
            CONSTRAINT fk_assessments_reviewer
                FOREIGN KEY (assigned_reviewer_id)
                REFERENCES users (id),
            CONSTRAINT uq_assessments_proc
                UNIQUE (processing_run_id)
        );
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION assessments_decided_freeze()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF OLD.decided_at IS NOT NULL THEN
                    RAISE EXCEPTION
                        'decided assessments cannot be deleted'
                        USING ERRCODE = '42501';
                END IF;
                RETURN OLD;
            END IF;

            IF OLD.decided_at IS NOT NULL THEN
                RAISE EXCEPTION
                    'decided assessments cannot be updated'
                    USING ERRCODE = '42501';
            END IF;

            RETURN NEW;
        END;
        $fn$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER assessments_decided_freeze_trg
            BEFORE UPDATE OR DELETE ON assessments
            FOR EACH ROW
            EXECUTE FUNCTION assessments_decided_freeze();
        """
    )

    op.execute(
        """
        CREATE TABLE finding_dispositions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            application_id UUID NOT NULL,
            assessment_id UUID NOT NULL,
            verification_finding_id UUID NOT NULL,
            action finding_disposition_action NOT NULL,
            justification TEXT NOT NULL,
            created_by_user_id UUID NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT fk_finding_disp_app
                FOREIGN KEY (application_id)
                REFERENCES applications (id),
            CONSTRAINT fk_finding_disp_assessment
                FOREIGN KEY (assessment_id)
                REFERENCES assessments (id),
            CONSTRAINT fk_finding_disp_finding
                FOREIGN KEY (verification_finding_id)
                REFERENCES verification_findings (id),
            CONSTRAINT fk_finding_disp_user
                FOREIGN KEY (created_by_user_id)
                REFERENCES users (id),
            CONSTRAINT uq_finding_disposition
                UNIQUE (assessment_id, verification_finding_id),
            CONSTRAINT ck_finding_disp_justification
                CHECK (char_length(trim(justification)) >= 10)
        );
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION finding_dispositions_append_only()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
        BEGIN
            RAISE EXCEPTION
                'finding_dispositions is append-only'
                USING ERRCODE = '42501';
        END;
        $fn$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER finding_dispositions_append_only_trg
            BEFORE UPDATE OR DELETE ON finding_dispositions
            FOR EACH ROW
            EXECUTE FUNCTION finding_dispositions_append_only();
        """
    )
    op.execute("REVOKE UPDATE, DELETE ON finding_dispositions FROM PUBLIC;")

    op.execute(
        """
        CREATE TABLE field_corrections (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            application_id UUID NOT NULL,
            extracted_field_id UUID NOT NULL,
            assessment_id UUID NULL,
            corrected_value_text TEXT NULL,
            corrected_value_numeric NUMERIC(18, 2) NULL,
            corrected_value_date DATE NULL,
            reason TEXT NOT NULL,
            corrected_by_user_id UUID NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT fk_field_corrections_app
                FOREIGN KEY (application_id)
                REFERENCES applications (id),
            CONSTRAINT fk_field_corrections_field
                FOREIGN KEY (extracted_field_id)
                REFERENCES document_extracted_fields (id),
            CONSTRAINT fk_field_corrections_assessment
                FOREIGN KEY (assessment_id)
                REFERENCES assessments (id),
            CONSTRAINT fk_field_corrections_user
                FOREIGN KEY (corrected_by_user_id)
                REFERENCES users (id),
            CONSTRAINT ck_field_corrections_reason
                CHECK (char_length(trim(reason)) >= 10),
            CONSTRAINT ck_field_corrections_has_value
                CHECK (
                    corrected_value_text IS NOT NULL
                    OR corrected_value_numeric IS NOT NULL
                    OR corrected_value_date IS NOT NULL
                )
        );
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION field_corrections_append_only()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
        BEGIN
            RAISE EXCEPTION
                'field_corrections is append-only'
                USING ERRCODE = '42501';
        END;
        $fn$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER field_corrections_append_only_trg
            BEFORE UPDATE OR DELETE ON field_corrections
            FOR EACH ROW
            EXECUTE FUNCTION field_corrections_append_only();
        """
    )
    op.execute("REVOKE UPDATE, DELETE ON field_corrections FROM PUBLIC;")


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS field_corrections_append_only_trg "
        "ON field_corrections;"
    )
    op.execute("DROP FUNCTION IF EXISTS field_corrections_append_only();")
    op.execute("DROP TABLE IF EXISTS field_corrections;")

    op.execute(
        "DROP TRIGGER IF EXISTS finding_dispositions_append_only_trg "
        "ON finding_dispositions;"
    )
    op.execute("DROP FUNCTION IF EXISTS finding_dispositions_append_only();")
    op.execute("DROP TABLE IF EXISTS finding_dispositions;")

    op.execute(
        "DROP TRIGGER IF EXISTS assessments_decided_freeze_trg "
        "ON assessments;"
    )
    op.execute("DROP FUNCTION IF EXISTS assessments_decided_freeze();")
    op.execute("DROP TABLE IF EXISTS assessments;")

    op.execute("DROP TYPE IF EXISTS finding_disposition_action;")
    op.execute("DROP TYPE IF EXISTS assessment_recommendation;")
