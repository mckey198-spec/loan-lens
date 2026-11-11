"""Application financial calculation outputs (M09-T01).

Revision ID: 0010_financial_calculations
Revises: 0009_verification_findings
Create Date: 2026-11-05

Creates application_financial_calculations: one immutable calculation
package per processing run, bound to the same application via composite
FKs to processing_runs and application_input_snapshots (ADR-011).

Revokes UPDATE/DELETE from PUBLIC and blocks mutation via an append-only
trigger. No SQLAlchemy ORM models.

Note: revision id fits alembic_version.version_num VARCHAR(32);
filename remains 0010_financial_calculations.py.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010_financial_calculations"
down_revision: Union[str, Sequence[str], None] = "0009_verification_findings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE application_financial_calculations (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            application_id UUID NOT NULL,
            processing_run_id UUID NOT NULL,
            input_snapshot_id UUID NOT NULL,
            proposed_emi NUMERIC(18, 2) NOT NULL,
            total_obligations NUMERIC(18, 2) NOT NULL,
            foir_pct NUMERIC(5, 2) NOT NULL,
            foir_threshold_pct NUMERIC(5, 2) NOT NULL,
            foir_passed BOOLEAN NOT NULL,
            net_disposable_income NUMERIC(18, 2) NOT NULL,
            max_qualifying_loan_amount NUMERIC(18, 2) NOT NULL,
            calculation_details_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            claim_fence BIGINT NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT fk_fin_calc_app
                FOREIGN KEY (application_id)
                REFERENCES applications (id),
            CONSTRAINT fk_fin_calc_proc
                FOREIGN KEY (processing_run_id, application_id)
                REFERENCES processing_runs (id, application_id),
            CONSTRAINT fk_fin_calc_snap
                FOREIGN KEY (input_snapshot_id, application_id)
                REFERENCES application_input_snapshots (id, application_id),
            CONSTRAINT uq_fin_calc_proc
                UNIQUE (processing_run_id),
            CONSTRAINT ck_fin_calc_proposed_emi
                CHECK (proposed_emi >= 0),
            CONSTRAINT ck_fin_calc_total_obligations
                CHECK (total_obligations >= 0),
            CONSTRAINT ck_fin_calc_foir_pct
                CHECK (foir_pct >= 0),
            CONSTRAINT ck_fin_calc_foir_threshold_pct
                CHECK (
                    foir_threshold_pct > 0
                    AND foir_threshold_pct <= 100
                ),
            CONSTRAINT ck_fin_calc_max_qualifying_loan_amount
                CHECK (max_qualifying_loan_amount >= 0),
            CONSTRAINT ck_fin_calc_claim_fence
                CHECK (claim_fence >= 0)
        );
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION application_financial_calculations_append_only()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
        BEGIN
            RAISE EXCEPTION
                'application_financial_calculations is append-only'
                USING ERRCODE = '42501';
        END;
        $fn$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER application_financial_calculations_append_only_trg
            BEFORE UPDATE OR DELETE ON application_financial_calculations
            FOR EACH ROW
            EXECUTE FUNCTION application_financial_calculations_append_only();
        """
    )
    op.execute(
        "REVOKE UPDATE, DELETE ON application_financial_calculations FROM PUBLIC;"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS application_financial_calculations_append_only_trg "
        "ON application_financial_calculations;"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS application_financial_calculations_append_only();"
    )
    op.execute("DROP TABLE IF EXISTS application_financial_calculations;")
