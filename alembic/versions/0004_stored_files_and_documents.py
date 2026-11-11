"""Stored files, application documents, and failure tables.

Revision ID: 0004_stored_files_and_docs
Revises: 0003_applications_state
Create Date: 2026-11-05

Creates document_type, document_status, document_failure_event_kind, and
document_failure_resolution_kind enums; stored_files (insert-only);
application_documents with mandatory hash-equality trigger (ADR-021),
hash/stored_file_id immutability, and partial unique active-hash dedup;
document_failure_events and document_failure_resolutions (append-only).

processing_run_id on document_failure_events is nullable so intake
validation failures can be recorded before processing_runs exists (M05).
No malware_scan_status column: a stored_files row exists only after a
clean scan (Database Design §4.3 / ADR-025). No SQLAlchemy ORM models.

Note: revision id is shortened to fit alembic_version.version_num VARCHAR(32);
filename remains 0004_stored_files_and_documents.py (M04-T01).
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_stored_files_and_docs"
down_revision: Union[str, Sequence[str], None] = "0003_applications_state"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TYPE document_type AS ENUM (
            'identity_proof',
            'pan_card',
            'address_proof',
            'salary_slip',
            'bank_statement',
            'form_16',
            'itr',
            'gst_return',
            'business_proof',
            'existing_loan_statement',
            'unsupported',
            'other'
        );
        """
    )
    op.execute(
        """
        CREATE TYPE document_status AS ENUM (
            'uploaded',
            'classified',
            'extracting',
            'extracted',
            'failed',
            'unsupported',
            'replaced'
        );
        """
    )
    op.execute(
        """
        CREATE TYPE document_failure_event_kind AS ENUM (
            'marked_unsupported',
            'ocr_failed',
            'unreadable',
            'extraction_failed'
        );
        """
    )
    op.execute(
        """
        CREATE TYPE document_failure_resolution_kind AS ENUM (
            'replacement_requested',
            'replaced',
            'retry_requested',
            'accepted_insufficient_evidence'
        );
        """
    )

    op.execute(
        """
        CREATE TABLE stored_files (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            content_sha256 CHAR(64) NOT NULL,
            storage_uri TEXT NOT NULL,
            original_filename TEXT NOT NULL,
            mime_type TEXT NOT NULL,
            byte_size BIGINT NOT NULL,
            signature_ok BOOLEAN NOT NULL,
            malware_scanner TEXT NOT NULL,
            malware_scanned_at TIMESTAMPTZ NOT NULL,
            malware_signature_version TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT stored_files_content_sha256_key UNIQUE (content_sha256),
            CONSTRAINT stored_files_content_sha256_len_chk
                CHECK (char_length(content_sha256) = 64),
            CONSTRAINT stored_files_byte_size_positive_chk
                CHECK (byte_size > 0)
        );
        """
    )

    # Insert-only: block UPDATE/DELETE even for roles that retain table privileges.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION stored_files_immutable()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
        BEGIN
            RAISE EXCEPTION
                'stored_files is insert-only'
                USING ERRCODE = '42501';
        END;
        $fn$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER stored_files_immutable_trg
            BEFORE UPDATE OR DELETE ON stored_files
            FOR EACH ROW
            EXECUTE FUNCTION stored_files_immutable();
        """
    )
    op.execute("REVOKE UPDATE, DELETE ON stored_files FROM PUBLIC;")

    op.execute(
        """
        CREATE TABLE application_documents (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            application_id UUID NOT NULL REFERENCES applications (id),
            stored_file_id UUID NOT NULL REFERENCES stored_files (id),
            content_sha256 CHAR(64) NOT NULL,
            document_type document_type NOT NULL,
            status document_status NOT NULL,
            is_alternative_evidence BOOLEAN NOT NULL DEFAULT false,
            classification_confidence NUMERIC(5, 4) NULL,
            classification_method TEXT NULL,
            replaced_by_document_id UUID NULL,
            uploaded_by_user_id UUID NOT NULL REFERENCES users (id),
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT application_documents_id_application_id_key
                UNIQUE (id, application_id),
            CONSTRAINT application_documents_content_sha256_len_chk
                CHECK (char_length(content_sha256) = 64),
            CONSTRAINT application_documents_replaced_by_fk
                FOREIGN KEY (replaced_by_document_id, application_id)
                REFERENCES application_documents (id, application_id)
        );
        """
    )
    op.execute(
        """
        CREATE INDEX application_documents_application_id_document_type_idx
            ON application_documents (application_id, document_type);
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_app_docs_active_content
            ON application_documents (application_id, content_sha256)
            WHERE status <> 'replaced' AND replaced_by_document_id IS NULL;
        """
    )

    # Mandatory hash equality on insert (ADR-021). Not optional.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION application_documents_hash_equality()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
        DECLARE
            stored_hash CHAR(64);
        BEGIN
            SELECT sf.content_sha256
              INTO stored_hash
              FROM stored_files AS sf
             WHERE sf.id = NEW.stored_file_id;

            IF stored_hash IS NULL THEN
                RAISE EXCEPTION
                    'application_documents.stored_file_id % not found in stored_files',
                    NEW.stored_file_id
                    USING ERRCODE = '23503';
            END IF;

            IF NEW.content_sha256 IS DISTINCT FROM stored_hash THEN
                RAISE EXCEPTION
                    'application_documents.content_sha256 must equal '
                    'stored_files.content_sha256 for stored_file_id'
                    USING ERRCODE = '23514';
            END IF;

            RETURN NEW;
        END;
        $fn$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER application_documents_hash_equality_trg
            BEFORE INSERT ON application_documents
            FOR EACH ROW
            EXECUTE FUNCTION application_documents_hash_equality();
        """
    )

    # Hash and stored_file_id are immutable; replacement is a new row.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION application_documents_hash_immutable()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
        BEGIN
            IF NEW.content_sha256 IS DISTINCT FROM OLD.content_sha256
               OR NEW.stored_file_id IS DISTINCT FROM OLD.stored_file_id THEN
                RAISE EXCEPTION
                    'application_documents.content_sha256 and stored_file_id '
                    'are immutable'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $fn$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER application_documents_hash_immutable_trg
            BEFORE UPDATE ON application_documents
            FOR EACH ROW
            EXECUTE FUNCTION application_documents_hash_immutable();
        """
    )

    op.execute(
        """
        CREATE TABLE document_failure_events (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            application_id UUID NOT NULL REFERENCES applications (id),
            application_document_id UUID NOT NULL,
            processing_run_id UUID NULL,
            kind document_failure_event_kind NOT NULL,
            affected_pages_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            reason TEXT NOT NULL,
            actor_type audit_actor_type NOT NULL,
            actor_user_id UUID NULL REFERENCES users (id),
            claim_fence BIGINT NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT document_failure_events_id_application_id_key
                UNIQUE (id, application_id),
            CONSTRAINT document_failure_events_run_doc_kind_key
                UNIQUE (processing_run_id, application_document_id, kind),
            CONSTRAINT document_failure_events_document_fk
                FOREIGN KEY (application_document_id, application_id)
                REFERENCES application_documents (id, application_id),
            CONSTRAINT document_failure_events_reason_nonempty_chk
                CHECK (length(trim(reason)) > 0)
        );
        """
    )
    op.execute(
        """
        CREATE INDEX document_failure_events_document_id_created_at_idx
            ON document_failure_events (application_document_id, created_at);
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION document_failure_events_append_only()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
        BEGIN
            RAISE EXCEPTION
                'document_failure_events is append-only'
                USING ERRCODE = '42501';
        END;
        $fn$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER document_failure_events_append_only_trg
            BEFORE UPDATE OR DELETE ON document_failure_events
            FOR EACH ROW
            EXECUTE FUNCTION document_failure_events_append_only();
        """
    )
    op.execute(
        "REVOKE UPDATE, DELETE ON document_failure_events FROM PUBLIC;"
    )

    op.execute(
        """
        CREATE TABLE document_failure_resolutions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            application_id UUID NOT NULL REFERENCES applications (id),
            document_failure_event_id UUID NOT NULL,
            application_document_id UUID NOT NULL,
            kind document_failure_resolution_kind NOT NULL,
            reason TEXT NOT NULL,
            actor_type audit_actor_type NOT NULL,
            actor_user_id UUID NULL REFERENCES users (id),
            replacement_document_id UUID NULL,
            created_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT document_failure_resolutions_event_fk
                FOREIGN KEY (document_failure_event_id, application_id)
                REFERENCES document_failure_events (id, application_id),
            CONSTRAINT document_failure_resolutions_document_fk
                FOREIGN KEY (application_document_id, application_id)
                REFERENCES application_documents (id, application_id),
            CONSTRAINT document_failure_resolutions_replacement_fk
                FOREIGN KEY (replacement_document_id, application_id)
                REFERENCES application_documents (id, application_id),
            CONSTRAINT document_failure_resolutions_replaced_chk
                CHECK (
                    kind <> 'replaced'
                    OR replacement_document_id IS NOT NULL
                ),
            CONSTRAINT document_failure_resolutions_reason_nonempty_chk
                CHECK (length(trim(reason)) > 0)
        );
        """
    )
    op.execute(
        """
        CREATE INDEX document_failure_resolutions_event_id_created_at_idx
            ON document_failure_resolutions (
                document_failure_event_id,
                created_at
            );
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION document_failure_resolutions_append_only()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
        BEGIN
            RAISE EXCEPTION
                'document_failure_resolutions is append-only'
                USING ERRCODE = '42501';
        END;
        $fn$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER document_failure_resolutions_append_only_trg
            BEFORE UPDATE OR DELETE ON document_failure_resolutions
            FOR EACH ROW
            EXECUTE FUNCTION document_failure_resolutions_append_only();
        """
    )
    op.execute(
        "REVOKE UPDATE, DELETE ON document_failure_resolutions FROM PUBLIC;"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS document_failure_resolutions_append_only_trg "
        "ON document_failure_resolutions;"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS document_failure_resolutions_append_only();"
    )
    op.execute("DROP TABLE IF EXISTS document_failure_resolutions;")

    op.execute(
        "DROP TRIGGER IF EXISTS document_failure_events_append_only_trg "
        "ON document_failure_events;"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS document_failure_events_append_only();"
    )
    op.execute("DROP TABLE IF EXISTS document_failure_events;")

    op.execute(
        "DROP TRIGGER IF EXISTS application_documents_hash_immutable_trg "
        "ON application_documents;"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS application_documents_hash_immutable();"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS application_documents_hash_equality_trg "
        "ON application_documents;"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS application_documents_hash_equality();"
    )
    op.execute("DROP INDEX IF EXISTS uq_app_docs_active_content;")
    op.execute("DROP TABLE IF EXISTS application_documents;")

    op.execute(
        "DROP TRIGGER IF EXISTS stored_files_immutable_trg ON stored_files;"
    )
    op.execute("DROP FUNCTION IF EXISTS stored_files_immutable();")
    op.execute("DROP TABLE IF EXISTS stored_files;")

    op.execute("DROP TYPE IF EXISTS document_failure_resolution_kind;")
    op.execute("DROP TYPE IF EXISTS document_failure_event_kind;")
    op.execute("DROP TYPE IF EXISTS document_status;")
    op.execute("DROP TYPE IF EXISTS document_type;")
