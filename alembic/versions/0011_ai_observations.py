"""Labelled AI observations per processing run (M11 / BR-12).

Revision ID: 0011_ai_observations
Revises: 0010_financial_calculations
Create Date: 2026-11-05

Creates assessment_ai_observations: one immutable advisory observation
package per processing run, bound to the same application via composite
FK to processing_runs (ADR-011). Observations are labelled AI output
(is_ai_generated) and never findings (Architecture §8; ADR-014).

Revokes UPDATE/DELETE from PUBLIC and blocks mutation via an append-only
trigger. No SQLAlchemy ORM models.

Note: revision id fits alembic_version.version_num VARCHAR(32);
filename remains 0011_ai_observations.py.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011_ai_observations"
down_revision: Union[str, Sequence[str], None] = "0010_financial_calculations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE assessment_ai_observations (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            application_id UUID NOT NULL,
            processing_run_id UUID NOT NULL,
            model_id TEXT NOT NULL,
            observation_text TEXT NOT NULL,
            risk_factors_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            strengths_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            cited_chunk_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            is_ai_generated BOOLEAN NOT NULL DEFAULT true,
            claim_fence BIGINT NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT fk_ai_obs_app
                FOREIGN KEY (application_id)
                REFERENCES applications (id),
            CONSTRAINT fk_ai_obs_proc
                FOREIGN KEY (processing_run_id, application_id)
                REFERENCES processing_runs (id, application_id),
            CONSTRAINT uq_ai_obs_proc
                UNIQUE (processing_run_id),
            CONSTRAINT ck_ai_obs_model_id
                CHECK (length(trim(model_id)) > 0),
            CONSTRAINT ck_ai_obs_observation_text
                CHECK (length(trim(observation_text)) > 0),
            CONSTRAINT ck_ai_obs_claim_fence
                CHECK (claim_fence >= 0),
            CONSTRAINT ck_ai_obs_is_ai_generated
                CHECK (is_ai_generated = true)
        );
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION assessment_ai_observations_append_only()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
        BEGIN
            RAISE EXCEPTION
                'assessment_ai_observations is append-only'
                USING ERRCODE = '42501';
        END;
        $fn$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER assessment_ai_observations_append_only_trg
            BEFORE UPDATE OR DELETE ON assessment_ai_observations
            FOR EACH ROW
            EXECUTE FUNCTION assessment_ai_observations_append_only();
        """
    )
    op.execute(
        "REVOKE UPDATE, DELETE ON assessment_ai_observations FROM PUBLIC;"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS assessment_ai_observations_append_only_trg "
        "ON assessment_ai_observations;"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS assessment_ai_observations_append_only();"
    )
    op.execute("DROP TABLE IF EXISTS assessment_ai_observations;")
