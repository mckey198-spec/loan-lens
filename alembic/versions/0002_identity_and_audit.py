"""Identity tables, audit chain, and append_audit_event.

Revision ID: 0002_identity_and_audit
Revises: 0001_extensions
Create Date: 2026-11-04

Creates user_role and audit_actor_type enums; users; user_sessions;
audit_chain_state (singleton genesis row); audit_events; append_audit_event
(ADR-012 / Database Design §4.1 and §4.8).

users.email is VARCHAR(255) with CHECK (email = lower(email)); citext is not used.
No SQLAlchemy ORM models. application_id / assessment_id are UUID columns only
(applications / assessments tables do not exist yet).
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_identity_and_audit"
down_revision: Union[str, Sequence[str], None] = "0001_extensions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_GENESIS_HASH = "0000000000000000000000000000000000000000000000000000000000000000"


def upgrade() -> None:
    op.execute(
        """
        CREATE TYPE user_role AS ENUM (
            'loan_officer',
            'reviewer',
            'admin'
        );
        """
    )
    op.execute(
        """
        CREATE TYPE audit_actor_type AS ENUM (
            'user',
            'system'
        );
        """
    )

    op.execute(
        """
        CREATE TABLE users (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            email VARCHAR(255) NOT NULL,
            full_name TEXT NOT NULL,
            role user_role NOT NULL,
            password_hash TEXT NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT users_email_lower_chk CHECK (email = lower(email)),
            CONSTRAINT users_email_key UNIQUE (email)
        );
        """
    )
    op.execute("CREATE INDEX users_role_idx ON users (role);")
    op.execute("CREATE INDEX users_is_active_idx ON users (is_active);")

    op.execute(
        """
        CREATE TABLE user_sessions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users (id),
            token_hash CHAR(64) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            last_seen_at TIMESTAMPTZ NOT NULL,
            revoked_at TIMESTAMPTZ NULL,
            CONSTRAINT user_sessions_token_hash_key UNIQUE (token_hash),
            CONSTRAINT user_sessions_expires_after_created_chk
                CHECK (expires_at > created_at)
        );
        """
    )
    op.execute(
        """
        CREATE INDEX user_sessions_active_user_id_idx
            ON user_sessions (user_id)
            WHERE revoked_at IS NULL;
        """
    )

    op.execute(
        """
        CREATE TABLE audit_chain_state (
            id SMALLINT PRIMARY KEY,
            last_chain_seq BIGINT NOT NULL,
            last_event_hash CHAR(64) NOT NULL,
            CONSTRAINT audit_chain_state_singleton_chk CHECK (id = 1)
        );
        """
    )
    op.execute(
        f"""
        INSERT INTO audit_chain_state (id, last_chain_seq, last_event_hash)
        VALUES (1, 0, '{_GENESIS_HASH}');
        """
    )

    op.execute(
        """
        CREATE TABLE audit_events (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            chain_seq BIGINT NOT NULL,
            application_id UUID NULL,
            actor_type audit_actor_type NOT NULL,
            actor_user_id UUID NULL REFERENCES users (id),
            action TEXT NOT NULL,
            entity_type TEXT NULL,
            entity_id UUID NULL,
            rationale TEXT NULL,
            before_json JSONB NULL,
            after_json JSONB NULL,
            assessment_id UUID NULL,
            request_id TEXT NULL,
            prev_event_hash CHAR(64) NOT NULL,
            event_hash CHAR(64) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT audit_events_chain_seq_key UNIQUE (chain_seq),
            CONSTRAINT audit_events_event_hash_key UNIQUE (event_hash)
        );
        """
    )
    op.execute(
        """
        CREATE INDEX audit_events_application_id_created_at_idx
            ON audit_events (application_id, created_at DESC);
        """
    )
    op.execute(
        """
        CREATE INDEX audit_events_action_created_at_idx
            ON audit_events (action, created_at DESC);
        """
    )

    # Session GUC gate: only append_audit_event may write these tables.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION audit_require_append_gate()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
        BEGIN
            IF current_setting('loanlens.allow_audit_append', true) IS DISTINCT FROM 'on' THEN
                RAISE EXCEPTION
                    'direct mutation of % is forbidden; use append_audit_event',
                    TG_TABLE_NAME
                    USING ERRCODE = '42501';
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $fn$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_events_append_only_trg
            BEFORE INSERT OR UPDATE OR DELETE ON audit_events
            FOR EACH ROW
            EXECUTE FUNCTION audit_require_append_gate();
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_chain_state_append_only_trg
            BEFORE INSERT OR UPDATE OR DELETE ON audit_chain_state
            FOR EACH ROW
            EXECUTE FUNCTION audit_require_append_gate();
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION append_audit_event(
            p_actor_type audit_actor_type,
            p_action TEXT,
            p_actor_user_id UUID DEFAULT NULL,
            p_application_id UUID DEFAULT NULL,
            p_entity_type TEXT DEFAULT NULL,
            p_entity_id UUID DEFAULT NULL,
            p_rationale TEXT DEFAULT NULL,
            p_before_json JSONB DEFAULT NULL,
            p_after_json JSONB DEFAULT NULL,
            p_assessment_id UUID DEFAULT NULL,
            p_request_id TEXT DEFAULT NULL
        )
        RETURNS UUID
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public
        AS $fn$
        DECLARE
            v_last_chain_seq BIGINT;
            v_prev_event_hash CHAR(64);
            v_chain_seq BIGINT;
            v_event_id UUID;
            v_created_at TIMESTAMPTZ;
            v_payload JSONB;
            v_event_hash CHAR(64);
        BEGIN
            IF p_action IS NULL OR length(trim(p_action)) = 0 THEN
                RAISE EXCEPTION 'append_audit_event: action is required'
                    USING ERRCODE = '22023';
            END IF;

            SELECT last_chain_seq, last_event_hash
              INTO v_last_chain_seq, v_prev_event_hash
              FROM audit_chain_state
             WHERE id = 1
             FOR UPDATE;

            IF NOT FOUND THEN
                RAISE EXCEPTION 'append_audit_event: audit_chain_state singleton missing'
                    USING ERRCODE = 'P0002';
            END IF;

            v_chain_seq := v_last_chain_seq + 1;
            v_event_id := gen_random_uuid();
            v_created_at := clock_timestamp();

            -- Canonical JSON: jsonb text form sorts keys and drops insignificant whitespace.
            v_payload := jsonb_build_object(
                'action', p_action,
                'actor_type', p_actor_type::text,
                'actor_user_id', p_actor_user_id,
                'after_json', p_after_json,
                'application_id', p_application_id,
                'assessment_id', p_assessment_id,
                'before_json', p_before_json,
                'chain_seq', v_chain_seq,
                'created_at',
                    to_char(v_created_at AT TIME ZONE 'UTC',
                            'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),
                'entity_id', p_entity_id,
                'entity_type', p_entity_type,
                'id', v_event_id,
                'prev_event_hash', v_prev_event_hash,
                'rationale', p_rationale,
                'request_id', p_request_id
            );
            v_event_hash := encode(digest(v_payload::text, 'sha256'), 'hex');

            PERFORM set_config('loanlens.allow_audit_append', 'on', true);

            INSERT INTO audit_events (
                id,
                chain_seq,
                application_id,
                actor_type,
                actor_user_id,
                action,
                entity_type,
                entity_id,
                rationale,
                before_json,
                after_json,
                assessment_id,
                request_id,
                prev_event_hash,
                event_hash,
                created_at
            ) VALUES (
                v_event_id,
                v_chain_seq,
                p_application_id,
                p_actor_type,
                p_actor_user_id,
                p_action,
                p_entity_type,
                p_entity_id,
                p_rationale,
                p_before_json,
                p_after_json,
                p_assessment_id,
                p_request_id,
                v_prev_event_hash,
                v_event_hash,
                v_created_at
            );

            UPDATE audit_chain_state
               SET last_chain_seq = v_chain_seq,
                   last_event_hash = v_event_hash
             WHERE id = 1;

            RETURN v_event_id;
        END;
        $fn$;
        """
    )

    # General application callers must not DML these tables directly (ADR-012).
    op.execute("REVOKE INSERT, UPDATE, DELETE ON audit_events FROM PUBLIC;")
    op.execute("REVOKE INSERT, UPDATE, DELETE ON audit_chain_state FROM PUBLIC;")
    op.execute("GRANT EXECUTE ON FUNCTION append_audit_event("
               "audit_actor_type, TEXT, UUID, UUID, TEXT, UUID, TEXT, JSONB, JSONB, UUID, TEXT"
               ") TO PUBLIC;")


def downgrade() -> None:
    op.execute(
        "DROP FUNCTION IF EXISTS append_audit_event("
        "audit_actor_type, TEXT, UUID, UUID, TEXT, UUID, TEXT, JSONB, JSONB, UUID, TEXT"
        ");"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS audit_chain_state_append_only_trg ON audit_chain_state;"
    )
    op.execute("DROP TRIGGER IF EXISTS audit_events_append_only_trg ON audit_events;")
    op.execute("DROP FUNCTION IF EXISTS audit_require_append_gate();")
    op.execute("DROP TABLE IF EXISTS audit_events;")
    op.execute("DROP TABLE IF EXISTS audit_chain_state;")
    op.execute("DROP TABLE IF EXISTS user_sessions;")
    op.execute("DROP TABLE IF EXISTS users;")
    op.execute("DROP TYPE IF EXISTS audit_actor_type;")
    op.execute("DROP TYPE IF EXISTS user_role;")
