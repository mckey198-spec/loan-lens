"""Processing runs, workflow checkpoints, and node events.

Revision ID: 0005_processing_runs_queue
Revises: 0004_stored_files_and_docs
Create Date: 2026-11-05

Creates processing_run_status enum; processing_runs (ADR-006 / ADR-015);
workflow_checkpoints with fence and freeze triggers; workflow_node_events
(append-only). Partial unique indexes enforce one active run and
idempotency keys per application (Database Design §4.4).

input_snapshot_id is nullable without FK until application_input_snapshots
exists (M06). No Redis/Celery. No SQLAlchemy ORM models.

Note: revision id is shortened to fit alembic_version.version_num VARCHAR(32);
filename remains 0005_processing_runs_and_checkpoints.py (M05-T01).
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_processing_runs_queue"
down_revision: Union[str, Sequence[str], None] = "0004_stored_files_and_docs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TYPE processing_run_status AS ENUM (
            'queued',
            'running',
            'succeeded',
            'failed',
            'cancelled'
        );
        """
    )

    op.execute(
        """
        CREATE TABLE processing_runs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            application_id UUID NOT NULL REFERENCES applications (id),
            input_snapshot_id UUID NULL,
            status processing_run_status NOT NULL,
            attempt_number INTEGER NOT NULL,
            trigger TEXT NOT NULL,
            idempotency_key TEXT NULL,
            fence_token BIGINT NOT NULL DEFAULT 0,
            locked_by TEXT NULL,
            locked_at TIMESTAMPTZ NULL,
            lease_expires_at TIMESTAMPTZ NULL,
            started_at TIMESTAMPTZ NULL,
            finished_at TIMESTAMPTZ NULL,
            error_code TEXT NULL,
            error_message TEXT NULL,
            provider_snapshot_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT processing_runs_id_application_id_key
                UNIQUE (id, application_id),
            CONSTRAINT processing_runs_attempt_number_chk
                CHECK (attempt_number >= 1),
            CONSTRAINT processing_runs_fence_token_chk
                CHECK (fence_token >= 0),
            CONSTRAINT processing_runs_succeeded_finished_chk
                CHECK (
                    (status <> 'succeeded')
                    OR (finished_at IS NOT NULL)
                ),
            CONSTRAINT processing_runs_failed_error_code_chk
                CHECK (
                    (status <> 'failed')
                    OR (error_code IS NOT NULL)
                ),
            CONSTRAINT processing_runs_trigger_nonempty_chk
                CHECK (length(trim(trigger)) > 0)
        );
        """
    )

    op.execute(
        """
        CREATE UNIQUE INDEX uq_processing_runs_active
            ON processing_runs (application_id)
            WHERE status IN ('queued', 'running');
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_processing_runs_idempotency
            ON processing_runs (application_id, idempotency_key)
            WHERE idempotency_key IS NOT NULL;
        """
    )
    op.execute(
        """
        CREATE INDEX processing_runs_status_lease_expires_at_idx
            ON processing_runs (status, lease_expires_at);
        """
    )
    op.execute(
        """
        CREATE INDEX processing_runs_application_id_created_at_idx
            ON processing_runs (application_id, created_at DESC);
        """
    )

    op.execute(
        """
        CREATE TABLE workflow_checkpoints (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            application_id UUID NOT NULL,
            processing_run_id UUID NOT NULL,
            claim_fence BIGINT NOT NULL,
            node_name TEXT NOT NULL,
            sequence_no INTEGER NOT NULL,
            status TEXT NOT NULL,
            state_json JSONB NOT NULL,
            error_code TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT workflow_checkpoints_run_application_fk
                FOREIGN KEY (processing_run_id, application_id)
                REFERENCES processing_runs (id, application_id),
            CONSTRAINT workflow_checkpoints_run_node_name_key
                UNIQUE (processing_run_id, node_name),
            CONSTRAINT workflow_checkpoints_run_sequence_no_key
                UNIQUE (processing_run_id, sequence_no),
            CONSTRAINT workflow_checkpoints_node_name_nonempty_chk
                CHECK (length(trim(node_name)) > 0),
            CONSTRAINT workflow_checkpoints_status_nonempty_chk
                CHECK (length(trim(status)) > 0)
        );
        """
    )

    # ADR-015: claim_fence must equal the parent run's current fence_token.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION workflow_checkpoints_fence_enforcement()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
        DECLARE
            parent_fence BIGINT;
        BEGIN
            SELECT fence_token
              INTO parent_fence
              FROM processing_runs
             WHERE id = NEW.processing_run_id
               AND application_id = NEW.application_id
             FOR SHARE;

            IF NOT FOUND THEN
                RAISE EXCEPTION
                    'STALE_WORKER_FENCE: processing_run not found for checkpoint'
                    USING ERRCODE = 'P0001';
            END IF;

            IF NEW.claim_fence IS DISTINCT FROM parent_fence THEN
                RAISE EXCEPTION
                    'STALE_WORKER_FENCE: claim_fence (%) does not match '
                    'processing_runs.fence_token (%)'
                    , NEW.claim_fence, parent_fence
                    USING ERRCODE = 'P0001';
            END IF;

            RETURN NEW;
        END;
        $fn$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER workflow_checkpoints_fence_enforcement_trg
            BEFORE INSERT OR UPDATE ON workflow_checkpoints
            FOR EACH ROW
            EXECUTE FUNCTION workflow_checkpoints_fence_enforcement();
        """
    )

    # Freeze checkpoints once the parent run has finished.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION workflow_checkpoints_freeze()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
        DECLARE
            parent_status processing_run_status;
        BEGIN
            SELECT status
              INTO parent_status
              FROM processing_runs
             WHERE id = NEW.processing_run_id
               AND application_id = NEW.application_id
             FOR SHARE;

            IF NOT FOUND THEN
                RAISE EXCEPTION
                    'workflow_checkpoints update rejected: processing_run not found'
                    USING ERRCODE = '23503';
            END IF;

            IF parent_status IN (
                'succeeded'::processing_run_status,
                'failed'::processing_run_status,
                'cancelled'::processing_run_status
            ) THEN
                RAISE EXCEPTION
                    'workflow_checkpoints is frozen when processing_runs.status '
                    'is %', parent_status
                    USING ERRCODE = '23514';
            END IF;

            RETURN NEW;
        END;
        $fn$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER workflow_checkpoints_freeze_trg
            BEFORE UPDATE ON workflow_checkpoints
            FOR EACH ROW
            EXECUTE FUNCTION workflow_checkpoints_freeze();
        """
    )

    op.execute(
        """
        CREATE TABLE workflow_node_events (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            application_id UUID NOT NULL,
            processing_run_id UUID NOT NULL,
            node_name TEXT NOT NULL,
            status TEXT NOT NULL,
            detail_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT workflow_node_events_run_application_fk
                FOREIGN KEY (processing_run_id, application_id)
                REFERENCES processing_runs (id, application_id),
            CONSTRAINT workflow_node_events_node_name_nonempty_chk
                CHECK (length(trim(node_name)) > 0),
            CONSTRAINT workflow_node_events_status_nonempty_chk
                CHECK (length(trim(status)) > 0)
        );
        """
    )
    op.execute(
        """
        CREATE INDEX workflow_node_events_run_created_at_idx
            ON workflow_node_events (processing_run_id, created_at);
        """
    )
    op.execute(
        "REVOKE UPDATE, DELETE ON workflow_node_events FROM PUBLIC;"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS workflow_node_events_run_created_at_idx;")
    op.execute("DROP TABLE IF EXISTS workflow_node_events;")

    op.execute(
        "DROP TRIGGER IF EXISTS workflow_checkpoints_freeze_trg "
        "ON workflow_checkpoints;"
    )
    op.execute("DROP FUNCTION IF EXISTS workflow_checkpoints_freeze();")
    op.execute(
        "DROP TRIGGER IF EXISTS workflow_checkpoints_fence_enforcement_trg "
        "ON workflow_checkpoints;"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS workflow_checkpoints_fence_enforcement();"
    )
    op.execute("DROP TABLE IF EXISTS workflow_checkpoints;")

    op.execute(
        "DROP INDEX IF EXISTS processing_runs_application_id_created_at_idx;"
    )
    op.execute(
        "DROP INDEX IF EXISTS processing_runs_status_lease_expires_at_idx;"
    )
    op.execute("DROP INDEX IF EXISTS uq_processing_runs_idempotency;")
    op.execute("DROP INDEX IF EXISTS uq_processing_runs_active;")
    op.execute("DROP TABLE IF EXISTS processing_runs;")

    op.execute("DROP TYPE IF EXISTS processing_run_status;")
