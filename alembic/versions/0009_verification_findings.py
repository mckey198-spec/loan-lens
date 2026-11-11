"""Verification runs and immutable deterministic findings (M08-T01).

Revision ID: 0009_verification_findings
Revises: 0007_document_extractions
Create Date: 2026-11-05

Creates rule_status and rule_severity enums; verification_runs (one per
processing run, composite FKs to processing_runs and input snapshots —
ADR-011); verification_findings (append-only rule results keyed by
verification_run_id + rule_code).

Both tables revoke UPDATE/DELETE from PUBLIC and block mutation via triggers.
No SQLAlchemy ORM models. No disposition columns (ADR-014 dispositions land later).

Note: revision id fits alembic_version.version_num VARCHAR(32);
filename remains 0009_verification_findings.py (M08-T01).
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009_verification_findings"
down_revision: Union[str, Sequence[str], None] = "0007_document_extractions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TYPE rule_status AS ENUM (
            'passed',
            'flagged',
            'failed'
        );
        """
    )
    op.execute(
        """
        CREATE TYPE rule_severity AS ENUM (
            'info',
            'low',
            'medium',
            'high',
            'critical'
        );
        """
    )

    op.execute(
        """
        CREATE TABLE verification_runs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            application_id UUID NOT NULL,
            processing_run_id UUID NOT NULL,
            input_snapshot_id UUID NOT NULL,
            total_rules_evaluated INTEGER NOT NULL,
            passed_count INTEGER NOT NULL,
            flagged_count INTEGER NOT NULL,
            failed_count INTEGER NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT verification_runs_id_application_id_key
                UNIQUE (id, application_id),
            CONSTRAINT fk_verif_runs_app
                FOREIGN KEY (application_id)
                REFERENCES applications (id),
            CONSTRAINT fk_verif_runs_proc
                FOREIGN KEY (processing_run_id, application_id)
                REFERENCES processing_runs (id, application_id),
            CONSTRAINT fk_verif_runs_snap
                FOREIGN KEY (input_snapshot_id, application_id)
                REFERENCES application_input_snapshots (id, application_id),
            CONSTRAINT uq_verif_runs_app_proc
                UNIQUE (processing_run_id),
            CONSTRAINT ck_verif_runs_counts_nonneg
                CHECK (
                    total_rules_evaluated >= 0
                    AND passed_count >= 0
                    AND flagged_count >= 0
                    AND failed_count >= 0
                ),
            CONSTRAINT ck_verif_runs_counts_sum
                CHECK (
                    passed_count + flagged_count + failed_count
                    = total_rules_evaluated
                )
        );
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION verification_runs_append_only()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
        BEGIN
            RAISE EXCEPTION
                'verification_runs is append-only'
                USING ERRCODE = '42501';
        END;
        $fn$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER verification_runs_append_only_trg
            BEFORE UPDATE OR DELETE ON verification_runs
            FOR EACH ROW
            EXECUTE FUNCTION verification_runs_append_only();
        """
    )
    op.execute("REVOKE UPDATE, DELETE ON verification_runs FROM PUBLIC;")

    op.execute(
        """
        CREATE TABLE verification_findings (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            application_id UUID NOT NULL,
            verification_run_id UUID NOT NULL,
            rule_code TEXT NOT NULL,
            rule_name TEXT NOT NULL,
            status rule_status NOT NULL,
            severity rule_severity NOT NULL,
            message TEXT NOT NULL,
            diff_payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            provenance_sources_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            claim_fence BIGINT NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT fk_verif_findings_run
                FOREIGN KEY (verification_run_id, application_id)
                REFERENCES verification_runs (id, application_id),
            CONSTRAINT uq_findings_run_rule
                UNIQUE (verification_run_id, rule_code),
            CONSTRAINT ck_verif_findings_rule_code
                CHECK (length(trim(rule_code)) > 0),
            CONSTRAINT ck_verif_findings_rule_name
                CHECK (length(trim(rule_name)) > 0),
            CONSTRAINT ck_verif_findings_message
                CHECK (length(trim(message)) > 0),
            CONSTRAINT ck_verif_findings_claim_fence
                CHECK (claim_fence >= 0)
        );
        """
    )

    op.execute(
        """
        CREATE INDEX verification_findings_application_id_created_at_idx
            ON verification_findings (application_id, created_at DESC);
        """
    )
    op.execute(
        """
        CREATE INDEX verification_findings_rule_code_idx
            ON verification_findings (rule_code);
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION verification_findings_append_only()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
        BEGIN
            RAISE EXCEPTION
                'verification_findings is append-only'
                USING ERRCODE = '42501';
        END;
        $fn$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER verification_findings_append_only_trg
            BEFORE UPDATE OR DELETE ON verification_findings
            FOR EACH ROW
            EXECUTE FUNCTION verification_findings_append_only();
        """
    )
    op.execute("REVOKE UPDATE, DELETE ON verification_findings FROM PUBLIC;")


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS verification_findings_append_only_trg "
        "ON verification_findings;"
    )
    op.execute("DROP FUNCTION IF EXISTS verification_findings_append_only();")
    op.execute(
        "DROP INDEX IF EXISTS verification_findings_rule_code_idx;"
    )
    op.execute(
        "DROP INDEX IF EXISTS verification_findings_application_id_created_at_idx;"
    )
    op.execute("DROP TABLE IF EXISTS verification_findings;")

    op.execute(
        "DROP TRIGGER IF EXISTS verification_runs_append_only_trg "
        "ON verification_runs;"
    )
    op.execute("DROP FUNCTION IF EXISTS verification_runs_append_only();")
    op.execute("DROP TABLE IF EXISTS verification_runs;")

    op.execute("DROP TYPE IF EXISTS rule_severity;")
    op.execute("DROP TYPE IF EXISTS rule_status;")
