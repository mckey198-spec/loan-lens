"""Worker queue schemas (ADR-006 / ADR-015)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

ProcessingRunStatus = Literal[
    "queued",
    "running",
    "succeeded",
    "failed",
    "cancelled",
]

TerminalRunStatus = Literal["succeeded", "failed"]

RunTrigger = Literal[
    "submit",
    "retry",
    "new_document",
    "correction",
    "documents_ready",
    "reopen_reprocess",
]


class RunClaim(BaseModel):
    """Result of a successful claim or reclaim of a processing run."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    application_id: UUID
    input_snapshot_id: UUID | None
    status: Literal["running"]
    attempt_number: int
    trigger: str
    idempotency_key: str | None
    fence_token: int = Field(ge=0)
    locked_by: str
    locked_at: datetime
    lease_expires_at: datetime
    started_at: datetime | None
    provider_snapshot_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    @property
    def claim_fence(self) -> int:
        """Fence token held by this claim; required on renewals and side effects."""
        return self.fence_token


CheckpointStatus = Literal["succeeded", "failed", "skipped"]


class CheckpointRecord(BaseModel):
    """One workflow_checkpoints row used for resume (Database Design §4.4)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    application_id: UUID
    processing_run_id: UUID
    claim_fence: int = Field(ge=0)
    node_name: str
    sequence_no: int = Field(ge=1)
    status: CheckpointStatus
    state_json: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    created_at: datetime
    updated_at: datetime
