"""Read-only audit hash-chain integrity verifier (Database Design §4.8; ADR-012)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Genesis hash seeded in audit_chain_state (64 zero hex digits).
GENESIS_EVENT_HASH = "0" * 64

# Recompute event_hash exactly as append_audit_event does (pgcrypto SHA-256 over
# jsonb_build_object(...)::text with sorted keys and UTC millisecond timestamps).
_RECOMPUTE_HASH_SQL = """
SELECT
    e.id,
    e.chain_seq,
    e.prev_event_hash,
    e.event_hash,
    encode(
        digest(
            jsonb_build_object(
                'action', e.action,
                'actor_type', e.actor_type::text,
                'actor_user_id', e.actor_user_id,
                'after_json', e.after_json,
                'application_id', e.application_id,
                'assessment_id', e.assessment_id,
                'before_json', e.before_json,
                'chain_seq', e.chain_seq,
                'created_at', to_char(
                    e.created_at AT TIME ZONE 'UTC',
                    'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'
                ),
                'entity_id', e.entity_id,
                'entity_type', e.entity_type,
                'id', e.id,
                'prev_event_hash', e.prev_event_hash,
                'rationale', e.rationale,
                'request_id', e.request_id
            )::text,
            'sha256'
        ),
        'hex'
    ) AS recomputed_hash
  FROM audit_events AS e
 ORDER BY e.chain_seq ASC
"""


class AuditVerificationReport(BaseModel):
    """Result of a read-only walk of the global audit chain."""

    model_config = ConfigDict(extra="forbid")

    is_valid: bool
    total_events_checked: int = Field(ge=0)
    tampered_event_id: UUID | None = None
    error_message: str | None = None


def _fail(
    *,
    total_events_checked: int,
    tampered_event_id: UUID | None,
    error_message: str,
) -> AuditVerificationReport:
    return AuditVerificationReport(
        is_valid=False,
        total_events_checked=total_events_checked,
        tampered_event_id=tampered_event_id,
        error_message=error_message,
    )


async def verify_audit_log_integrity(
    connection: Any,
) -> AuditVerificationReport:
    """
    Recompute the global SHA-256 audit hash chain from genesis to head.

    Rejects gaps, payload/hash tampering, broken prev links, and a singleton
    tail that disagrees with ``audit_chain_state``. Does not repair.
    """
    state_row = await connection.execute(
        """
        SELECT last_chain_seq, last_event_hash
          FROM audit_chain_state
         WHERE id = 1
        """
    )
    state = await state_row.fetchone()
    if state is None:
        return _fail(
            total_events_checked=0,
            tampered_event_id=None,
            error_message="audit_chain_state singleton row is missing",
        )

    events_row = await connection.execute(_RECOMPUTE_HASH_SQL)
    events = list(await events_row.fetchall())
    total = len(events)

    if total == 0:
        if int(state["last_chain_seq"]) != 0 or state["last_event_hash"] != GENESIS_EVENT_HASH:
            return _fail(
                total_events_checked=0,
                tampered_event_id=None,
                error_message=(
                    "empty audit_events disagrees with audit_chain_state "
                    f"(last_chain_seq={state['last_chain_seq']}, "
                    f"last_event_hash={state['last_event_hash']})"
                ),
            )
        return AuditVerificationReport(
            is_valid=True,
            total_events_checked=0,
            tampered_event_id=None,
            error_message=None,
        )

    expected_prev = GENESIS_EVENT_HASH
    expected_seq = 1

    for event in events:
        event_id: UUID = event["id"]
        chain_seq = int(event["chain_seq"])
        prev_hash = str(event["prev_event_hash"])
        stored_hash = str(event["event_hash"])
        recomputed = str(event["recomputed_hash"])

        if chain_seq != expected_seq:
            return _fail(
                total_events_checked=total,
                tampered_event_id=event_id,
                error_message=(
                    f"missing or out-of-order chain_seq at event {event_id}: "
                    f"expected {expected_seq}, found {chain_seq}"
                ),
            )

        if prev_hash != expected_prev:
            return _fail(
                total_events_checked=total,
                tampered_event_id=event_id,
                error_message=(
                    f"broken chain link at event {event_id}: "
                    f"prev_event_hash does not match prior event_hash"
                ),
            )

        if stored_hash != recomputed:
            return _fail(
                total_events_checked=total,
                tampered_event_id=event_id,
                error_message=(
                    f"tampered payload or event_hash at event {event_id}: "
                    "stored hash does not match recomputed SHA-256"
                ),
            )

        expected_prev = stored_hash
        expected_seq += 1

    last = events[-1]
    last_seq = int(last["chain_seq"])
    last_hash = str(last["event_hash"])
    if int(state["last_chain_seq"]) != last_seq or state["last_event_hash"] != last_hash:
        return _fail(
            total_events_checked=total,
            tampered_event_id=last["id"],
            error_message=(
                "audit_chain_state tail disagrees with last audit_events row "
                f"(state seq={state['last_chain_seq']} hash={state['last_event_hash']}; "
                f"head seq={last_seq} hash={last_hash})"
            ),
        )

    return AuditVerificationReport(
        is_valid=True,
        total_events_checked=total,
        tampered_event_id=None,
        error_message=None,
    )
