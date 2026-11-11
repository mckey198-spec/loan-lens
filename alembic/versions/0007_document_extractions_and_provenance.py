"""Document extractions, page extents, and structured provenance.

Revision ID: 0007_document_extractions
Revises: 0006_policies_snapshots
Create Date: 2026-11-05

Creates extraction_field_name and extraction_status enums;
document_page_extents; document_extractions (run-scoped header);
document_extracted_fields (append-only field facts with optional bbox);
salary_slip_extractions and bank_statement_monthly_summary (append-only
structured views).

Composite FKs keep documents, runs, and extraction children on the same
application (ADR-011). Extracted fields and structured summaries revoke
UPDATE/DELETE and block mutation via triggers.

No SQLAlchemy ORM models. No embedding columns.

Note: revision id is shortened to fit alembic_version.version_num VARCHAR(32);
filename remains 0007_document_extractions_and_provenance.py (M07-T01).
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007_document_extractions"
down_revision: Union[str, Sequence[str], None] = "0006_policies_snapshots"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TYPE extraction_field_name AS ENUM (
            'applicant_name',
            'pan',
            'date_of_birth',
            'employer_name',
            'gross_salary',
            'net_salary',
            'statement_start_date',
            'statement_end_date',
            'account_number',
            'ifsc',
            'average_monthly_balance',
            'total_credits',
            'total_debits'
        );
        """
    )
    op.execute(
        """
        CREATE TYPE extraction_status AS ENUM (
            'pending',
            'completed',
            'failed'
        );
        """
    )

    op.execute(
        """
        CREATE TABLE document_page_extents (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            application_id UUID NOT NULL,
            application_document_id UUID NOT NULL,
            page_number INTEGER NOT NULL,
            width_pts NUMERIC(10, 2) NOT NULL,
            height_pts NUMERIC(10, 2) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT fk_page_extents_doc
                FOREIGN KEY (application_document_id, application_id)
                REFERENCES application_documents (id, application_id),
            CONSTRAINT uq_page_extents_doc_page
                UNIQUE (application_document_id, page_number),
            CONSTRAINT ck_page_extents_page_number
                CHECK (page_number >= 1),
            CONSTRAINT ck_page_extents_width_pts
                CHECK (width_pts > 0),
            CONSTRAINT ck_page_extents_height_pts
                CHECK (height_pts > 0)
        );
        """
    )

    op.execute(
        """
        CREATE TABLE document_extractions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            application_id UUID NOT NULL,
            application_document_id UUID NOT NULL,
            processing_run_id UUID NOT NULL,
            extractor_name TEXT NOT NULL,
            extractor_version TEXT NOT NULL,
            status extraction_status NOT NULL DEFAULT 'pending',
            error_code TEXT NULL,
            error_message TEXT NULL,
            raw_payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT document_extractions_id_application_id_key
                UNIQUE (id, application_id),
            CONSTRAINT fk_extractions_doc
                FOREIGN KEY (application_document_id, application_id)
                REFERENCES application_documents (id, application_id),
            CONSTRAINT fk_extractions_run
                FOREIGN KEY (processing_run_id, application_id)
                REFERENCES processing_runs (id, application_id),
            CONSTRAINT uq_extractions_doc_run
                UNIQUE (application_document_id, processing_run_id)
        );
        """
    )

    op.execute(
        """
        CREATE TABLE document_extracted_fields (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            application_id UUID NOT NULL,
            extraction_id UUID NOT NULL,
            field_name extraction_field_name NOT NULL,
            raw_value TEXT NOT NULL,
            normalized_value_json JSONB NULL,
            confidence NUMERIC(5, 4) NULL,
            page_number INTEGER NULL,
            bbox_x_min NUMERIC(10, 4) NULL,
            bbox_y_min NUMERIC(10, 4) NULL,
            bbox_x_max NUMERIC(10, 4) NULL,
            bbox_y_max NUMERIC(10, 4) NULL,
            created_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT fk_extracted_fields_extraction
                FOREIGN KEY (extraction_id, application_id)
                REFERENCES document_extractions (id, application_id),
            CONSTRAINT uq_extracted_fields_name
                UNIQUE (extraction_id, field_name),
            CONSTRAINT ck_extracted_fields_confidence
                CHECK (
                    confidence IS NULL
                    OR (confidence >= 0 AND confidence <= 1)
                ),
            CONSTRAINT ck_extracted_fields_page_number
                CHECK (page_number IS NULL OR page_number >= 1)
        );
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION document_extracted_fields_append_only()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
        BEGIN
            RAISE EXCEPTION
                'document_extracted_fields is append-only'
                USING ERRCODE = '42501';
        END;
        $fn$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER document_extracted_fields_append_only_trg
            BEFORE UPDATE OR DELETE ON document_extracted_fields
            FOR EACH ROW
            EXECUTE FUNCTION document_extracted_fields_append_only();
        """
    )
    op.execute(
        "REVOKE UPDATE, DELETE ON document_extracted_fields FROM PUBLIC;"
    )

    op.execute(
        """
        CREATE TABLE salary_slip_extractions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            application_id UUID NOT NULL,
            extraction_id UUID NOT NULL,
            pay_period_month DATE NOT NULL,
            basic_pay NUMERIC(18, 2) NULL,
            allowances NUMERIC(18, 2) NULL,
            deductions NUMERIC(18, 2) NULL,
            net_pay NUMERIC(18, 2) NOT NULL,
            employer_name TEXT NOT NULL,
            employee_pan CHAR(10) NULL,
            created_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT fk_salary_slip_extraction
                FOREIGN KEY (extraction_id, application_id)
                REFERENCES document_extractions (id, application_id),
            CONSTRAINT uq_salary_slip_extraction
                UNIQUE (extraction_id),
            CONSTRAINT ck_salary_slip_basic_pay
                CHECK (basic_pay IS NULL OR basic_pay >= 0),
            CONSTRAINT ck_salary_slip_allowances
                CHECK (allowances IS NULL OR allowances >= 0),
            CONSTRAINT ck_salary_slip_deductions
                CHECK (deductions IS NULL OR deductions >= 0),
            CONSTRAINT ck_salary_slip_net_pay
                CHECK (net_pay >= 0)
        );
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION salary_slip_extractions_append_only()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
        BEGIN
            RAISE EXCEPTION
                'salary_slip_extractions is append-only'
                USING ERRCODE = '42501';
        END;
        $fn$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER salary_slip_extractions_append_only_trg
            BEFORE UPDATE OR DELETE ON salary_slip_extractions
            FOR EACH ROW
            EXECUTE FUNCTION salary_slip_extractions_append_only();
        """
    )
    op.execute(
        "REVOKE UPDATE, DELETE ON salary_slip_extractions FROM PUBLIC;"
    )

    op.execute(
        """
        CREATE TABLE bank_statement_monthly_summary (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            application_id UUID NOT NULL,
            extraction_id UUID NOT NULL,
            statement_month DATE NOT NULL,
            total_credits NUMERIC(18, 2) NOT NULL,
            total_debits NUMERIC(18, 2) NOT NULL,
            closing_balance NUMERIC(18, 2) NOT NULL,
            identified_salary_credits NUMERIC(18, 2) NOT NULL DEFAULT 0,
            salary_credit_count INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT fk_bank_summary_extraction
                FOREIGN KEY (extraction_id, application_id)
                REFERENCES document_extractions (id, application_id),
            CONSTRAINT uq_bank_summary_month
                UNIQUE (extraction_id, statement_month),
            CONSTRAINT ck_bank_summary_total_credits
                CHECK (total_credits >= 0),
            CONSTRAINT ck_bank_summary_total_debits
                CHECK (total_debits >= 0),
            CONSTRAINT ck_bank_summary_salary_credits
                CHECK (identified_salary_credits >= 0),
            CONSTRAINT ck_bank_summary_salary_count
                CHECK (salary_credit_count >= 0)
        );
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION bank_statement_monthly_summary_append_only()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
        BEGIN
            RAISE EXCEPTION
                'bank_statement_monthly_summary is append-only'
                USING ERRCODE = '42501';
        END;
        $fn$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER bank_statement_monthly_summary_append_only_trg
            BEFORE UPDATE OR DELETE ON bank_statement_monthly_summary
            FOR EACH ROW
            EXECUTE FUNCTION bank_statement_monthly_summary_append_only();
        """
    )
    op.execute(
        "REVOKE UPDATE, DELETE ON bank_statement_monthly_summary FROM PUBLIC;"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS bank_statement_monthly_summary_append_only_trg "
        "ON bank_statement_monthly_summary;"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS bank_statement_monthly_summary_append_only();"
    )
    op.execute("DROP TABLE IF EXISTS bank_statement_monthly_summary;")

    op.execute(
        "DROP TRIGGER IF EXISTS salary_slip_extractions_append_only_trg "
        "ON salary_slip_extractions;"
    )
    op.execute("DROP FUNCTION IF EXISTS salary_slip_extractions_append_only();")
    op.execute("DROP TABLE IF EXISTS salary_slip_extractions;")

    op.execute(
        "DROP TRIGGER IF EXISTS document_extracted_fields_append_only_trg "
        "ON document_extracted_fields;"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS document_extracted_fields_append_only();"
    )
    op.execute("DROP TABLE IF EXISTS document_extracted_fields;")

    op.execute("DROP TABLE IF EXISTS document_extractions;")
    op.execute("DROP TABLE IF EXISTS document_page_extents;")

    op.execute("DROP TYPE IF EXISTS extraction_status;")
    op.execute("DROP TYPE IF EXISTS extraction_field_name;")
