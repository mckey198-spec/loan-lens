"""Policy store and application input snapshots.

Revision ID: 0006_policies_snapshots
Revises: 0005_processing_runs_queue
Create Date: 2026-11-05

Creates policy_status enum; policy_documents; policy_versions with
non-overlapping active daterange exclusion (ADR-016); policy_threshold_configs;
policy_document_requirements; policy_chunks with generated content_tsv + GIN
(lexical only — no embedding, ADR-020); application_input_snapshots;
snapshot_calculation_inputs (ADR-009 / ADR-011 composite FK).

Wires applications.applicable_policy_version_id and
processing_runs (input_snapshot_id, application_id) FKs deferred from M03/M05.
Append-only controls on snapshots and calc inputs; active policy freeze trigger.

No SQLAlchemy ORM models. No policy_chunks.embedding.

Note: revision id is shortened to fit alembic_version.version_num VARCHAR(32);
filename remains 0006_policies_and_input_snapshots.py (M06-T01).
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006_policies_snapshots"
down_revision: Union[str, Sequence[str], None] = "0005_processing_runs_queue"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TYPE policy_status AS ENUM (
            'draft',
            'active',
            'archived'
        );
        """
    )

    op.execute(
        """
        CREATE TABLE policy_documents (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            title TEXT NOT NULL,
            lender_label TEXT NOT NULL,
            created_by_user_id UUID NOT NULL REFERENCES users (id),
            created_at TIMESTAMPTZ NOT NULL
        );
        """
    )

    op.execute(
        """
        CREATE TABLE policy_versions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            policy_document_id UUID NOT NULL
                REFERENCES policy_documents (id),
            version_label TEXT NOT NULL,
            status policy_status NOT NULL DEFAULT 'draft',
            annual_interest_rate_pct NUMERIC(5, 2) NOT NULL,
            effective_from DATE NOT NULL,
            effective_to DATE NULL,
            created_by_user_id UUID NOT NULL REFERENCES users (id),
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT ck_policy_versions_rate_pct
                CHECK (
                    annual_interest_rate_pct > 0
                    AND annual_interest_rate_pct <= 100
                ),
            CONSTRAINT ck_policy_versions_date_range
                CHECK (
                    effective_to IS NULL
                    OR effective_to >= effective_from
                ),
            CONSTRAINT uq_policy_document_version
                UNIQUE (policy_document_id, version_label)
        );
        """
    )

    # ADR-016: active versions must not overlap on inclusive date ranges,
    # globally across policy documents (btree_gist enabled in 0001).
    op.execute(
        """
        ALTER TABLE policy_versions
            ADD CONSTRAINT uq_policy_versions_active_no_overlap
            EXCLUDE USING gist (
                daterange(effective_from, effective_to, '[]') WITH &&
            )
            WHERE (status = 'active');
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION policy_versions_active_freeze()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF OLD.status = 'active'::policy_status THEN
                    RAISE EXCEPTION
                        'active policy_versions cannot be deleted'
                        USING ERRCODE = '23514';
                END IF;
                RETURN OLD;
            END IF;

            IF OLD.status = 'active'::policy_status THEN
                IF NEW.status = 'archived'::policy_status THEN
                    RETURN NEW;
                END IF;
                RAISE EXCEPTION
                    'active policy_versions cannot be updated '
                    '(only transition to archived is allowed)'
                    USING ERRCODE = '23514';
            END IF;

            RETURN NEW;
        END;
        $fn$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER policy_versions_active_freeze_trg
            BEFORE UPDATE OR DELETE ON policy_versions
            FOR EACH ROW
            EXECUTE FUNCTION policy_versions_active_freeze();
        """
    )

    op.execute(
        """
        CREATE TABLE policy_threshold_configs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            policy_version_id UUID NOT NULL
                REFERENCES policy_versions (id),
            applicant_type applicant_type NOT NULL,
            min_monthly_income NUMERIC(18, 2) NOT NULL,
            max_foir_pct NUMERIC(5, 2) NOT NULL,
            min_employment_months INTEGER NOT NULL,
            max_loan_amount NUMERIC(18, 2) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT ck_policy_thresholds_min_income
                CHECK (min_monthly_income >= 0),
            CONSTRAINT ck_policy_thresholds_max_foir
                CHECK (max_foir_pct > 0 AND max_foir_pct <= 100),
            CONSTRAINT ck_policy_thresholds_min_employment
                CHECK (min_employment_months >= 0),
            CONSTRAINT ck_policy_thresholds_max_loan
                CHECK (max_loan_amount > 0),
            CONSTRAINT uq_policy_thresholds_type
                UNIQUE (policy_version_id, applicant_type)
        );
        """
    )

    op.execute(
        """
        CREATE TABLE policy_document_requirements (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            policy_version_id UUID NOT NULL
                REFERENCES policy_versions (id),
            applicant_type applicant_type NOT NULL,
            document_type document_type NOT NULL,
            is_mandatory BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT uq_policy_doc_reqs
                UNIQUE (policy_version_id, applicant_type, document_type)
        );
        """
    )

    # Lexical retrieval only (ADR-020): content_tsv generated; no embedding.
    op.execute(
        """
        CREATE TABLE policy_chunks (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            policy_version_id UUID NOT NULL
                REFERENCES policy_versions (id),
            chunk_index INTEGER NOT NULL,
            section_label TEXT NOT NULL,
            content_text TEXT NOT NULL,
            content_tsv TSVECTOR GENERATED ALWAYS AS (
                to_tsvector('english', content_text)
            ) STORED,
            created_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT uq_policy_chunks_index
                UNIQUE (policy_version_id, chunk_index)
        );
        """
    )
    op.execute(
        """
        CREATE INDEX policy_chunks_content_tsv_gin_idx
            ON policy_chunks USING gin (content_tsv);
        """
    )

    op.execute(
        """
        CREATE TABLE application_input_snapshots (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            application_id UUID NOT NULL
                REFERENCES applications (id),
            snapshot_number INTEGER NOT NULL,
            snapshot_hash CHAR(64) NOT NULL,
            as_of_date DATE NOT NULL,
            applicant_type applicant_type NOT NULL,
            requested_amount NUMERIC(18, 2) NOT NULL,
            tenure_months INTEGER NOT NULL,
            loan_purpose TEXT NOT NULL,
            declared_gross_monthly_income NUMERIC(18, 2) NOT NULL,
            declared_net_monthly_income NUMERIC(18, 2) NOT NULL,
            declared_existing_emis NUMERIC(18, 2) NOT NULL DEFAULT 0,
            declared_other_income NUMERIC(18, 2) NULL,
            declared_household_expenses NUMERIC(18, 2) NULL,
            document_inventory_json JSONB NOT NULL,
            applicable_policy_version_id UUID NOT NULL
                REFERENCES policy_versions (id),
            created_by_user_id UUID NOT NULL REFERENCES users (id),
            created_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT uq_snapshots_app_id
                UNIQUE (id, application_id),
            CONSTRAINT uq_snapshots_number
                UNIQUE (application_id, snapshot_number),
            CONSTRAINT ck_snapshots_number
                CHECK (snapshot_number >= 1),
            CONSTRAINT ck_snapshots_requested_amount
                CHECK (requested_amount > 0),
            CONSTRAINT ck_snapshots_tenure
                CHECK (tenure_months > 0),
            CONSTRAINT ck_snapshots_gross_income
                CHECK (declared_gross_monthly_income >= 0),
            CONSTRAINT ck_snapshots_net_income
                CHECK (declared_net_monthly_income >= 0),
            CONSTRAINT ck_snapshots_existing_emis
                CHECK (declared_existing_emis >= 0),
            CONSTRAINT ck_snapshots_net_le_gross
                CHECK (
                    declared_net_monthly_income
                    <= declared_gross_monthly_income
                ),
            CONSTRAINT ck_snapshots_other_income
                CHECK (
                    declared_other_income IS NULL
                    OR declared_other_income >= 0
                ),
            CONSTRAINT ck_snapshots_household_expenses
                CHECK (
                    declared_household_expenses IS NULL
                    OR declared_household_expenses >= 0
                ),
            CONSTRAINT ck_snapshots_hash_len
                CHECK (char_length(snapshot_hash) = 64)
        );
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION application_input_snapshots_append_only()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
        BEGIN
            RAISE EXCEPTION
                'application_input_snapshots is append-only'
                USING ERRCODE = '42501';
        END;
        $fn$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER application_input_snapshots_append_only_trg
            BEFORE UPDATE OR DELETE ON application_input_snapshots
            FOR EACH ROW
            EXECUTE FUNCTION application_input_snapshots_append_only();
        """
    )
    op.execute(
        "REVOKE UPDATE, DELETE ON application_input_snapshots FROM PUBLIC;"
    )

    # ADR-009 / ADR-011: calc inputs freeze under the same application as the
    # parent snapshot via composite FK; one row per snapshot.
    op.execute(
        """
        CREATE TABLE snapshot_calculation_inputs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            application_id UUID NOT NULL,
            input_snapshot_id UUID NOT NULL,
            monthly_gross_income NUMERIC(18, 2) NOT NULL,
            monthly_net_income NUMERIC(18, 2) NOT NULL,
            monthly_obligations NUMERIC(18, 2) NOT NULL,
            requested_loan_amount NUMERIC(18, 2) NOT NULL,
            tenure_months INTEGER NOT NULL,
            annual_interest_rate_pct NUMERIC(5, 2) NOT NULL,
            max_allowed_foir_pct NUMERIC(5, 2) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT fk_calc_inputs_snapshot
                FOREIGN KEY (input_snapshot_id, application_id)
                REFERENCES application_input_snapshots (id, application_id),
            CONSTRAINT uq_calc_inputs_snapshot
                UNIQUE (input_snapshot_id),
            CONSTRAINT ck_calc_inputs_gross
                CHECK (monthly_gross_income >= 0),
            CONSTRAINT ck_calc_inputs_net
                CHECK (monthly_net_income >= 0),
            CONSTRAINT ck_calc_inputs_obligations
                CHECK (monthly_obligations >= 0),
            CONSTRAINT ck_calc_inputs_loan_amount
                CHECK (requested_loan_amount > 0),
            CONSTRAINT ck_calc_inputs_tenure
                CHECK (tenure_months > 0),
            CONSTRAINT ck_calc_inputs_rate
                CHECK (
                    annual_interest_rate_pct > 0
                    AND annual_interest_rate_pct <= 100
                ),
            CONSTRAINT ck_calc_inputs_foir
                CHECK (
                    max_allowed_foir_pct > 0
                    AND max_allowed_foir_pct <= 100
                )
        );
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION snapshot_calculation_inputs_append_only()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
        BEGIN
            RAISE EXCEPTION
                'snapshot_calculation_inputs is append-only'
                USING ERRCODE = '42501';
        END;
        $fn$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER snapshot_calculation_inputs_append_only_trg
            BEFORE UPDATE OR DELETE ON snapshot_calculation_inputs
            FOR EACH ROW
            EXECUTE FUNCTION snapshot_calculation_inputs_append_only();
        """
    )
    op.execute(
        "REVOKE UPDATE, DELETE ON snapshot_calculation_inputs FROM PUBLIC;"
    )

    op.execute(
        """
        ALTER TABLE applications
            ADD CONSTRAINT fk_apps_applicable_policy_version
            FOREIGN KEY (applicable_policy_version_id)
            REFERENCES policy_versions (id);
        """
    )

    op.execute(
        """
        ALTER TABLE processing_runs
            ADD CONSTRAINT fk_runs_input_snapshot
            FOREIGN KEY (input_snapshot_id, application_id)
            REFERENCES application_input_snapshots (id, application_id);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE processing_runs
            DROP CONSTRAINT IF EXISTS fk_runs_input_snapshot;
        """
    )
    op.execute(
        """
        ALTER TABLE applications
            DROP CONSTRAINT IF EXISTS fk_apps_applicable_policy_version;
        """
    )

    op.execute(
        "DROP TRIGGER IF EXISTS snapshot_calculation_inputs_append_only_trg "
        "ON snapshot_calculation_inputs;"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS snapshot_calculation_inputs_append_only();"
    )
    op.execute("DROP TABLE IF EXISTS snapshot_calculation_inputs;")

    op.execute(
        "DROP TRIGGER IF EXISTS application_input_snapshots_append_only_trg "
        "ON application_input_snapshots;"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS application_input_snapshots_append_only();"
    )
    op.execute("DROP TABLE IF EXISTS application_input_snapshots;")

    op.execute("DROP INDEX IF EXISTS policy_chunks_content_tsv_gin_idx;")
    op.execute("DROP TABLE IF EXISTS policy_chunks;")
    op.execute("DROP TABLE IF EXISTS policy_document_requirements;")
    op.execute("DROP TABLE IF EXISTS policy_threshold_configs;")

    op.execute(
        "DROP TRIGGER IF EXISTS policy_versions_active_freeze_trg "
        "ON policy_versions;"
    )
    op.execute("DROP FUNCTION IF EXISTS policy_versions_active_freeze();")
    op.execute("DROP TABLE IF EXISTS policy_versions;")
    op.execute("DROP TABLE IF EXISTS policy_documents;")
    op.execute("DROP TYPE IF EXISTS policy_status;")
