"""Append-only audit chain via append_audit_event (ADR-012)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb


async def append_audit_event(
    conn: AsyncConnection[Any],
    *,
    action: str,
    actor_type: str = "user",
    actor_user_id: UUID | None = None,
    application_id: UUID | None = None,
    entity_type: str | None = None,
    entity_id: UUID | None = None,
    rationale: str | None = None,
    before_json: dict[str, Any] | None = None,
    after_json: dict[str, Any] | None = None,
    assessment_id: UUID | None = None,
    request_id: str | None = None,
) -> UUID:
    """
    Call the SECURITY DEFINER function append_audit_event.

    Direct INSERT into audit_events is revoked; this is the only write path.
    """
    row = await conn.execute(
        """
        SELECT append_audit_event(
            %s::audit_actor_type,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s
        ) AS event_id
        """,
        (
            actor_type,
            action,
            actor_user_id,
            application_id,
            entity_type,
            entity_id,
            rationale,
            Jsonb(before_json) if before_json is not None else None,
            Jsonb(after_json) if after_json is not None else None,
            assessment_id,
            request_id,
        ),
    )
    result = await row.fetchone()
    assert result is not None
    return result["event_id"]
