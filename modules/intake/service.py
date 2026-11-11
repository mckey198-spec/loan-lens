"""Draft application intake service (Architecture §11; M03-T02 / M03-T03 / M04-T03)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import shutil
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname
from uuid import UUID, uuid4

from psycopg.errors import UniqueViolation
from psycopg_pool import AsyncConnectionPool

from apps.api.metrics import record_application_status
from modules.governance.audit import append_audit_event
from modules.intake import repository
from modules.intake.scanner import MalwareScanner, ScanStatus
from modules.intake.schemas import (
    ApplicationListResponse,
    ApplicationResponse,
    ApplicationStatus,
    ApplicantResponse,
    ApplicantUpdate,
    ArchiveRequest,
    CreateApplicationRequest,
    DocumentResponse,
    DocumentType,
    UpdateApplicationRequest,
    WithdrawRequest,
    require_loan_purpose_other,
)
from modules.intake.signatures import validate_file_signature
from modules.policy.service import select_active_policy_version_on_connection
from modules.worker import queue as worker_queue
from shared.errors import AppError
from shared.settings import Settings, get_settings

_PAN_PATTERN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
_SUBMIT_ALLOWED_ROLES: frozenset[str] = frozenset({"loan_officer", "admin"})
_SUBMIT_TRIGGER = "application_submission"

logger = logging.getLogger(__name__)

_VALID_STATUSES: frozenset[str] = frozenset(
    {
        "draft",
        "submitted",
        "processing",
        "processing_failed",
        "awaiting_human_review",
        "in_review",
        "clarification_required",
        "escalated",
        "review_completed",
        "archived",
    }
)

_WITHDRAW_ALLOWED_STATUSES: frozenset[str] = frozenset(
    {"draft", "processing_failed", "clarification_required"}
)
_ARCHIVE_ALLOWED_STATUSES: frozenset[str] = frozenset(
    {
        "awaiting_human_review",
        "review_completed",
        "draft",
        "processing_failed",
        "clarification_required",
    }
)

_UPLOAD_ALLOWED_STATUSES: frozenset[str] = frozenset(
    {"draft", "clarification_required", "processing_failed"}
)
_UPLOAD_ALLOWED_ROLES: frozenset[str] = frozenset({"loan_officer", "admin"})

ActorRole = Literal["loan_officer", "reviewer", "admin"]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _generate_reference_code() -> str:
    return f"LL-{uuid4().hex[:12].upper()}"


def _applicant_response(row: dict[str, Any]) -> ApplicantResponse:
    return ApplicantResponse(
        id=row["id"],
        application_id=row["application_id"],
        full_name=row["full_name"],
        date_of_birth=row["date_of_birth"],
        pan=row["pan"],
        current_address=row["current_address"],
        employer_or_business_name=row["employer_or_business_name"],
        time_in_job_or_business_months=row["time_in_job_or_business_months"],
        gross_monthly_income=row["gross_monthly_income"],
        net_monthly_income=row["net_monthly_income"],
        other_monthly_income=row["other_monthly_income"],
        other_monthly_income_evidenced=row["other_monthly_income_evidenced"],
        monthly_household_expenses=row["monthly_household_expenses"],
        total_existing_monthly_emis=row["total_existing_monthly_emis"],
        outstanding_existing_loan_amount=row["outstanding_existing_loan_amount"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _application_response(
    application: dict[str, Any],
    applicant: dict[str, Any],
) -> ApplicationResponse:
    return ApplicationResponse(
        id=application["id"],
        reference_code=application["reference_code"],
        status=application["status"],
        created_by_user_id=application["created_by_user_id"],
        assigned_reviewer_user_id=application["assigned_reviewer_user_id"],
        applicant_type=application["applicant_type"],
        requested_amount=application["requested_amount"],
        currency=application["currency"],
        tenure_months=application["tenure_months"],
        loan_purpose=application["loan_purpose"],
        loan_purpose_other=application["loan_purpose_other"],
        applicable_policy_version_id=application["applicable_policy_version_id"],
        withdraw_reason=application["withdraw_reason"],
        submitted_at=application["submitted_at"],
        archived_at=application["archived_at"],
        created_at=application["created_at"],
        updated_at=application["updated_at"],
        applicant=_applicant_response(applicant),
    )


async def create_draft_application(
    pool: AsyncConnectionPool,
    *,
    actor_user_id: UUID,
    payload: CreateApplicationRequest,
) -> ApplicationResponse:
    """Insert application + applicant + initial draft history in one transaction."""
    now = _utcnow()
    applicant = payload.applicant
    reference_code = _generate_reference_code()

    async with pool.connection() as conn:
        async with conn.transaction():
            application = await repository.insert_application(
                conn,
                reference_code=reference_code,
                created_by_user_id=actor_user_id,
                applicant_type=payload.applicant_type,
                requested_amount=payload.requested_amount,
                tenure_months=payload.tenure_months,
                loan_purpose=payload.loan_purpose,
                loan_purpose_other=payload.loan_purpose_other,
                created_at=now,
                updated_at=now,
            )
            applicant_row = await repository.insert_applicant(
                conn,
                application_id=application["id"],
                full_name=applicant.full_name,
                date_of_birth=applicant.date_of_birth,
                pan=applicant.pan,
                current_address=applicant.current_address,
                employer_or_business_name=applicant.employer_or_business_name,
                time_in_job_or_business_months=applicant.time_in_job_or_business_months,
                gross_monthly_income=applicant.gross_monthly_income,
                net_monthly_income=applicant.net_monthly_income,
                total_existing_monthly_emis=applicant.total_existing_monthly_emis,
                other_monthly_income=applicant.other_monthly_income,
                other_monthly_income_evidenced=applicant.other_monthly_income_evidenced,
                monthly_household_expenses=applicant.monthly_household_expenses,
                outstanding_existing_loan_amount=applicant.outstanding_existing_loan_amount,
                created_at=now,
                updated_at=now,
            )
            await repository.insert_state_history(
                conn,
                application_id=application["id"],
                from_status=None,
                to_status="draft",
                actor_user_id=actor_user_id,
                created_at=now,
            )
            record_application_status("draft")

    return _application_response(application, applicant_row)


async def get_application(
    pool: AsyncConnectionPool,
    *,
    application_id: UUID,
) -> ApplicationResponse:
    async with pool.connection() as conn:
        async with conn.transaction():
            application = await repository.fetch_application_by_id(conn, application_id)
            if application is None:
                raise AppError(
                    code="APPLICATION_NOT_FOUND",
                    message="Application not found.",
                    status_code=404,
                    details={"application_id": str(application_id)},
                )
            applicant = await repository.fetch_applicant_by_application_id(
                conn, application_id
            )
            if applicant is None:
                raise AppError(
                    code="APPLICANT_NOT_FOUND",
                    message="Applicant record not found for application.",
                    status_code=404,
                    details={"application_id": str(application_id)},
                )

    return _application_response(application, applicant)


async def list_applications(
    pool: AsyncConnectionPool,
    *,
    offset: int,
    limit: int,
    status: ApplicationStatus | None = None,
) -> ApplicationListResponse:
    if offset < 0:
        raise AppError(
            code="VALIDATION_ERROR",
            message="offset must be >= 0.",
            status_code=422,
        )
    if limit < 1 or limit > 100:
        raise AppError(
            code="VALIDATION_ERROR",
            message="limit must be between 1 and 100.",
            status_code=422,
        )
    if status is not None and status not in _VALID_STATUSES:
        raise AppError(
            code="VALIDATION_ERROR",
            message="status filter is not a valid application_status.",
            status_code=422,
            details={"status": status},
        )

    async with pool.connection() as conn:
        async with conn.transaction():
            applications, total = await repository.list_applications(
                conn,
                offset=offset,
                limit=limit,
                status=status,
            )
            applicant_map = await repository.fetch_applicants_by_application_ids(
                conn,
                [row["id"] for row in applications],
            )

    items: list[ApplicationResponse] = []
    for application in applications:
        applicant = applicant_map.get(application["id"])
        if applicant is None:
            raise AppError(
                code="APPLICANT_NOT_FOUND",
                message="Applicant record not found for application.",
                status_code=404,
                details={"application_id": str(application["id"])},
            )
        items.append(_application_response(application, applicant))

    return ApplicationListResponse(
        items=items,
        offset=offset,
        limit=limit,
        total=total,
    )


def _merged_loan_purpose_other(
    *,
    existing_purpose: str,
    existing_other: str | None,
    payload: UpdateApplicationRequest,
    purpose_set: bool,
    other_set: bool,
) -> tuple[str, str | None, bool]:
    """
    Return (purpose, other, clear_other).

    clear_other is True when the client explicitly nulls loan_purpose_other
    while purpose is not 'other'.
    """
    purpose = payload.loan_purpose if purpose_set and payload.loan_purpose is not None else existing_purpose
    if other_set:
        other = payload.loan_purpose_other
    else:
        other = existing_other
    try:
        other = require_loan_purpose_other(purpose, other)
    except ValueError as exc:
        raise AppError(
            code="VALIDATION_ERROR",
            message=str(exc),
            status_code=422,
        ) from exc

    clear_other = other_set and payload.loan_purpose_other is None and purpose != "other"
    return purpose, other, clear_other


def _merged_applicant_fields(
    *,
    existing: dict[str, Any],
    patch: ApplicantUpdate,
) -> dict[str, Any]:
    data = patch.model_dump(exclude_unset=True)
    if not data:
        return {}

    gross = data.get("gross_monthly_income", existing["gross_monthly_income"])
    net = data.get("net_monthly_income", existing["net_monthly_income"])
    if isinstance(gross, Decimal) and isinstance(net, Decimal) and net > gross:
        raise AppError(
            code="VALIDATION_ERROR",
            message="net_monthly_income must be <= gross_monthly_income",
            status_code=422,
        )
    return data


async def update_draft_application(
    pool: AsyncConnectionPool,
    *,
    application_id: UUID,
    payload: UpdateApplicationRequest,
) -> ApplicationResponse:
    application_fields = payload.model_dump(exclude_unset=True, exclude={"applicant"})
    applicant_patch = payload.applicant
    applicant_fields_unset = (
        applicant_patch.model_dump(exclude_unset=True) if applicant_patch is not None else {}
    )
    if not application_fields and not applicant_fields_unset:
        raise AppError(
            code="VALIDATION_ERROR",
            message="At least one field must be provided for update.",
            status_code=422,
        )

    now = _utcnow()

    async with pool.connection() as conn:
        async with conn.transaction():
            existing = await repository.fetch_application_by_id(conn, application_id)
            if existing is None:
                raise AppError(
                    code="APPLICATION_NOT_FOUND",
                    message="Application not found.",
                    status_code=404,
                    details={"application_id": str(application_id)},
                )
            if existing["status"] != "draft":
                raise AppError(
                    code="APPLICATION_NOT_DRAFT",
                    message="Only draft applications can be updated.",
                    status_code=409,
                    details={
                        "application_id": str(application_id),
                        "status": existing["status"],
                    },
                )

            existing_applicant = await repository.fetch_applicant_by_application_id(
                conn, application_id
            )
            if existing_applicant is None:
                raise AppError(
                    code="APPLICANT_NOT_FOUND",
                    message="Applicant record not found for application.",
                    status_code=404,
                    details={"application_id": str(application_id)},
                )

            purpose_set = "loan_purpose" in application_fields
            other_set = "loan_purpose_other" in application_fields
            _purpose, other, clear_other = _merged_loan_purpose_other(
                existing_purpose=existing["loan_purpose"],
                existing_other=existing["loan_purpose_other"],
                payload=payload,
                purpose_set=purpose_set,
                other_set=other_set,
            )

            application = existing
            if application_fields or clear_other:
                application_row = await repository.update_application(
                    conn,
                    application_id=application_id,
                    applicant_type=payload.applicant_type
                    if "applicant_type" in application_fields
                    else None,
                    requested_amount=payload.requested_amount
                    if "requested_amount" in application_fields
                    else None,
                    tenure_months=payload.tenure_months
                    if "tenure_months" in application_fields
                    else None,
                    loan_purpose=payload.loan_purpose
                    if purpose_set
                    else None,
                    loan_purpose_other=None if clear_other else other if other_set else None,
                    clear_loan_purpose_other=clear_other,
                    updated_at=now,
                )
                assert application_row is not None
                application = application_row

            applicant = existing_applicant
            if applicant_patch is not None:
                merged = _merged_applicant_fields(
                    existing=existing_applicant,
                    patch=applicant_patch,
                )
                if merged:
                    applicant_row = await repository.update_applicant(
                        conn,
                        application_id=application_id,
                        fields=merged,
                        updated_at=now,
                    )
                    assert applicant_row is not None
                    applicant = applicant_row

                # Keep application.updated_at in sync when only applicant changes.
                if not application_fields and not clear_other and merged:
                    application_row = await repository.update_application(
                        conn,
                        application_id=application_id,
                        applicant_type=None,
                        requested_amount=None,
                        tenure_months=None,
                        loan_purpose=None,
                        loan_purpose_other=None,
                        clear_loan_purpose_other=False,
                        updated_at=now,
                    )
                    assert application_row is not None
                    application = application_row

    return _application_response(application, applicant)


def _application_audit_snapshot(application: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(application["id"]),
        "status": application["status"],
        "withdraw_reason": application["withdraw_reason"],
        "archived_at": (
            application["archived_at"].isoformat()
            if application["archived_at"] is not None
            else None
        ),
    }


async def _transition_to_archived(
    pool: AsyncConnectionPool,
    *,
    application_id: UUID,
    actor_user_id: UUID,
    actor_role: ActorRole,
    reason: str,
    allowed_statuses: frozenset[str],
    audit_action: str,
    require_creator_for_loan_officer: bool,
    request_id: str | None = None,
) -> ApplicationResponse:
    now = _utcnow()

    async with pool.connection() as conn:
        async with conn.transaction():
            existing = await repository.lock_application_for_update(
                conn, application_id
            )
            if existing is None:
                raise AppError(
                    code="APPLICATION_NOT_FOUND",
                    message="Application not found.",
                    status_code=404,
                    details={"application_id": str(application_id)},
                )

            current_status = existing["status"]
            if current_status == "archived":
                raise AppError(
                    code="APPLICATION_ALREADY_ARCHIVED",
                    message="Application is already archived.",
                    status_code=409,
                    details={
                        "application_id": str(application_id),
                        "status": current_status,
                    },
                )
            if current_status not in allowed_statuses:
                raise AppError(
                    code="APPLICATION_INVALID_STATE",
                    message=(
                        "Application cannot be moved to archived from the "
                        "current status."
                    ),
                    status_code=409,
                    details={
                        "application_id": str(application_id),
                        "status": current_status,
                        "allowed_statuses": sorted(allowed_statuses),
                    },
                )

            if (
                require_creator_for_loan_officer
                and actor_role == "loan_officer"
                and existing["created_by_user_id"] != actor_user_id
            ):
                raise AppError(
                    code="AUTH_FORBIDDEN",
                    message="Only the creating loan officer may withdraw this application.",
                    status_code=403,
                    details={
                        "application_id": str(application_id),
                        "role": actor_role,
                    },
                )

            await repository.insert_state_history(
                conn,
                application_id=application_id,
                from_status=current_status,
                to_status="archived",
                actor_user_id=actor_user_id,
                reason=reason,
                created_at=now,
            )
            application = await repository.archive_application(
                conn,
                application_id=application_id,
                withdraw_reason=reason,
                archived_at=now,
                updated_at=now,
            )
            assert application is not None

            applicant = await repository.fetch_applicant_by_application_id(
                conn, application_id
            )
            if applicant is None:
                raise AppError(
                    code="APPLICANT_NOT_FOUND",
                    message="Applicant record not found for application.",
                    status_code=404,
                    details={"application_id": str(application_id)},
                )

            await append_audit_event(
                conn,
                action=audit_action,
                actor_type="user",
                actor_user_id=actor_user_id,
                application_id=application_id,
                entity_type="application",
                entity_id=application_id,
                rationale=reason,
                before_json=_application_audit_snapshot(existing),
                after_json=_application_audit_snapshot(application),
                request_id=request_id,
            )

    return _application_response(application, applicant)


async def withdraw_application(
    pool: AsyncConnectionPool,
    *,
    application_id: UUID,
    actor_user_id: UUID,
    actor_role: ActorRole,
    payload: WithdrawRequest,
    request_id: str | None = None,
) -> ApplicationResponse:
    """Archive via withdrawal from people-owned withdrawable states."""
    return await _transition_to_archived(
        pool,
        application_id=application_id,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        reason=payload.reason,
        allowed_statuses=_WITHDRAW_ALLOWED_STATUSES,
        audit_action="application.withdraw",
        require_creator_for_loan_officer=True,
        request_id=request_id,
    )


async def archive_application(
    pool: AsyncConnectionPool,
    *,
    application_id: UUID,
    actor_user_id: UUID,
    payload: ArchiveRequest,
    request_id: str | None = None,
) -> ApplicationResponse:
    """Admin archival from permitted terminal/withdrawable states."""
    return await _transition_to_archived(
        pool,
        application_id=application_id,
        actor_user_id=actor_user_id,
        actor_role="admin",
        reason=payload.reason,
        allowed_statuses=_ARCHIVE_ALLOWED_STATUSES,
        audit_action="application.archive",
        require_creator_for_loan_officer=False,
        request_id=request_id,
    )


def _decimal_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")


def _canonical_snapshot_hash(payload: dict[str, Any]) -> str:
    """SHA-256 over sorted canonical JSON (no insignificant whitespace)."""
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _build_document_inventory(
    documents: list[dict[str, Any]],
) -> list[dict[str, str]]:
    inventory = [
        {
            "document_id": str(doc["id"]),
            "document_type": str(doc["document_type"]),
            "content_sha256": str(doc["content_sha256"]),
        }
        for doc in documents
    ]
    inventory.sort(key=lambda item: item["document_id"])
    return inventory


def _validate_applicant_for_submit(applicant: dict[str, Any]) -> None:
    pan = str(applicant.get("pan") or "").strip().upper()
    if not _PAN_PATTERN.fullmatch(pan):
        raise AppError(
            code="INVALID_APPLICANT_PAN",
            message="Applicant PAN is missing or invalid.",
            status_code=422,
            details={"pan_present": bool(applicant.get("pan"))},
        )
    for field in (
        "gross_monthly_income",
        "net_monthly_income",
        "total_existing_monthly_emis",
    ):
        value = applicant.get(field)
        if value is None:
            raise AppError(
                code="INVALID_APPLICANT_INCOME",
                message="Applicant declared incomes are incomplete.",
                status_code=422,
                details={"missing_field": field},
            )


async def submit_application(
    pool: AsyncConnectionPool,
    *,
    application_id: UUID,
    actor_user_id: UUID,
    actor_role: ActorRole,
    request_id: str | None = None,
) -> ApplicationResponse:
    """
    Atomic draft → submitted hand-off (Architecture §11 Submit).

    Locks the application, freezes the input snapshot and calculation inputs,
    sets status to submitted, enqueues a queued processing run, and audits.
    """
    if actor_role not in _SUBMIT_ALLOWED_ROLES:
        raise AppError(
            code="AUTH_FORBIDDEN",
            message="Reviewers cannot submit applications.",
            status_code=403,
            details={"role": actor_role},
        )

    now = _utcnow()
    as_of_date: date = now.date()

    async with pool.connection() as conn:
        async with conn.transaction():
            existing = await repository.lock_application_for_update(
                conn, application_id
            )
            if existing is None:
                raise AppError(
                    code="APPLICATION_NOT_FOUND",
                    message="Application not found.",
                    status_code=404,
                    details={"application_id": str(application_id)},
                )

            if (
                actor_role == "loan_officer"
                and existing["created_by_user_id"] != actor_user_id
            ):
                raise AppError(
                    code="AUTH_FORBIDDEN",
                    message="Only the creating loan officer may submit this application.",
                    status_code=403,
                    details={
                        "application_id": str(application_id),
                        "role": actor_role,
                    },
                )

            current_status = existing["status"]
            if current_status != "draft":
                raise AppError(
                    code="APPLICATION_NOT_DRAFT",
                    message="Only draft applications can be submitted.",
                    status_code=409,
                    details={
                        "application_id": str(application_id),
                        "status": current_status,
                    },
                )

            applicant = await repository.fetch_applicant_by_application_id(
                conn, application_id
            )
            if applicant is None:
                raise AppError(
                    code="APPLICANT_NOT_FOUND",
                    message="Applicant record not found for application.",
                    status_code=404,
                    details={"application_id": str(application_id)},
                )
            _validate_applicant_for_submit(applicant)

            documents = await repository.list_non_replaced_documents_with_files(
                conn, application_id
            )
            if not documents:
                raise AppError(
                    code="NO_DOCUMENTS_UPLOADED",
                    message="At least one active document is required before submit.",
                    status_code=422,
                    details={"application_id": str(application_id)},
                )

            policy = await select_active_policy_version_on_connection(
                conn,
                as_of_date=as_of_date,
            )
            if policy is None:
                raise AppError(
                    code="NO_ACTIVE_POLICY_FOUND",
                    message="No active policy version covers the submission as-of date.",
                    status_code=422,
                    details={"as_of_date": as_of_date.isoformat()},
                )

            threshold = next(
                (
                    row
                    for row in policy.thresholds
                    if row.applicant_type == existing["applicant_type"]
                ),
                None,
            )
            if threshold is None:
                raise AppError(
                    code="NO_POLICY_THRESHOLD_FOR_APPLICANT_TYPE",
                    message=(
                        "Active policy has no threshold config for the "
                        "application's applicant type."
                    ),
                    status_code=422,
                    details={
                        "policy_version_id": str(policy.id),
                        "applicant_type": existing["applicant_type"],
                    },
                )

            inventory = _build_document_inventory(documents)
            snapshot_number = await repository.next_snapshot_number(
                conn, application_id
            )
            hash_payload: dict[str, Any] = {
                "application_id": str(application_id),
                "applicable_policy_version_id": str(policy.id),
                "applicant_type": existing["applicant_type"],
                "as_of_date": as_of_date.isoformat(),
                "declared_existing_emis": _decimal_str(
                    applicant["total_existing_monthly_emis"]
                ),
                "declared_gross_monthly_income": _decimal_str(
                    applicant["gross_monthly_income"]
                ),
                "declared_household_expenses": _decimal_str(
                    applicant["monthly_household_expenses"]
                ),
                "declared_net_monthly_income": _decimal_str(
                    applicant["net_monthly_income"]
                ),
                "declared_other_income": _decimal_str(
                    applicant["other_monthly_income"]
                ),
                "document_inventory": inventory,
                "loan_purpose": existing["loan_purpose"],
                "requested_amount": _decimal_str(existing["requested_amount"]),
                "snapshot_number": snapshot_number,
                "tenure_months": existing["tenure_months"],
            }
            snapshot_hash = _canonical_snapshot_hash(hash_payload)

            snapshot = await repository.insert_application_input_snapshot(
                conn,
                application_id=application_id,
                snapshot_number=snapshot_number,
                snapshot_hash=snapshot_hash,
                as_of_date=as_of_date,
                applicant_type=existing["applicant_type"],
                requested_amount=existing["requested_amount"],
                tenure_months=existing["tenure_months"],
                loan_purpose=existing["loan_purpose"],
                declared_gross_monthly_income=applicant["gross_monthly_income"],
                declared_net_monthly_income=applicant["net_monthly_income"],
                declared_existing_emis=applicant["total_existing_monthly_emis"],
                declared_other_income=applicant["other_monthly_income"],
                declared_household_expenses=applicant[
                    "monthly_household_expenses"
                ],
                document_inventory_json=inventory,
                applicable_policy_version_id=policy.id,
                created_by_user_id=actor_user_id,
                created_at=now,
            )

            await repository.insert_snapshot_calculation_inputs(
                conn,
                application_id=application_id,
                input_snapshot_id=snapshot["id"],
                monthly_gross_income=applicant["gross_monthly_income"],
                monthly_net_income=applicant["net_monthly_income"],
                monthly_obligations=applicant["total_existing_monthly_emis"],
                requested_loan_amount=existing["requested_amount"],
                tenure_months=existing["tenure_months"],
                annual_interest_rate_pct=policy.annual_interest_rate_pct,
                max_allowed_foir_pct=threshold.max_foir_pct,
                created_at=now,
            )

            await repository.insert_state_history(
                conn,
                application_id=application_id,
                from_status="draft",
                to_status="submitted",
                actor_user_id=actor_user_id,
                created_at=now,
            )
            application = await repository.mark_application_submitted(
                conn,
                application_id=application_id,
                applicable_policy_version_id=policy.id,
                updated_at=now,
            )
            assert application is not None

            run_id = await worker_queue.enqueue_run(
                conn,
                application_id,
                trigger=_SUBMIT_TRIGGER,
                input_snapshot_id=snapshot["id"],
            )

            await append_audit_event(
                conn,
                action="application.submit",
                actor_type="user",
                actor_user_id=actor_user_id,
                application_id=application_id,
                entity_type="applications",
                entity_id=application_id,
                before_json=_application_audit_snapshot(existing),
                after_json={
                    **_application_audit_snapshot(application),
                    "applicable_policy_version_id": str(policy.id),
                    "input_snapshot_id": str(snapshot["id"]),
                    "processing_run_id": str(run_id),
                    "snapshot_hash": snapshot_hash,
                },
                request_id=request_id,
            )
            record_application_status("submitted")

    return _application_response(application, applicant)


def _purge_path(path: Path) -> None:
    try:
        if path.is_file():
            path.unlink()
    except OSError:
        logger.warning("failed to purge path %s", path, exc_info=True)


def _document_response(
    document: dict[str, Any],
    stored_file: dict[str, Any],
) -> DocumentResponse:
    return DocumentResponse(
        id=document["id"],
        application_id=document["application_id"],
        content_sha256=document["content_sha256"],
        document_type=document["document_type"],
        status=document["status"],
        original_filename=stored_file["original_filename"],
        mime_type=stored_file["mime_type"],
        byte_size=stored_file["byte_size"],
        is_alternative_evidence=document["is_alternative_evidence"],
        uploaded_by_user_id=document["uploaded_by_user_id"],
        created_at=document["created_at"],
        updated_at=document["updated_at"],
    )


def _document_response_from_joined(row: dict[str, Any]) -> DocumentResponse:
    """Map a joined document+file row to API metadata. Never includes storage_uri."""
    return DocumentResponse(
        id=row["id"],
        application_id=row["application_id"],
        content_sha256=row["content_sha256"],
        document_type=row["document_type"],
        status=row["status"],
        original_filename=row["original_filename"],
        mime_type=row["mime_type"],
        byte_size=row["byte_size"],
        is_alternative_evidence=row["is_alternative_evidence"],
        uploaded_by_user_id=row["uploaded_by_user_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _path_from_storage_uri(storage_uri: str) -> Path:
    """Resolve an internal file URI or plain path to a filesystem Path."""
    parsed = urlparse(storage_uri)
    if parsed.scheme == "file":
        return Path(url2pathname(unquote(parsed.path)))
    return Path(storage_uri)


@dataclass(frozen=True)
class DocumentContent:
    """Internal download payload. storage_uri is never exposed on this type."""

    document_id: UUID
    application_id: UUID
    path: Path
    mime_type: str
    original_filename: str
    byte_size: int


async def list_documents(
    pool: AsyncConnectionPool,
    *,
    application_id: UUID,
) -> list[DocumentResponse]:
    """List non-replaced documents for an application (metadata only)."""
    async with pool.connection() as conn:
        async with conn.transaction():
            application = await repository.fetch_application_by_id(conn, application_id)
            if application is None:
                raise AppError(
                    code="APPLICATION_NOT_FOUND",
                    message="Application not found.",
                    status_code=404,
                    details={"application_id": str(application_id)},
                )
            rows = await repository.list_non_replaced_documents_with_files(
                conn, application_id
            )
    return [_document_response_from_joined(row) for row in rows]


async def get_document(
    pool: AsyncConnectionPool,
    *,
    application_id: UUID,
    document_id: UUID,
) -> DocumentResponse:
    """Return document metadata when document_id belongs to application_id."""
    async with pool.connection() as conn:
        async with conn.transaction():
            row = await repository.fetch_document_with_file(
                conn,
                application_id=application_id,
                document_id=document_id,
            )
            if row is None:
                raise AppError(
                    code="DOCUMENT_NOT_FOUND",
                    message="Document not found for this application.",
                    status_code=404,
                    details={
                        "application_id": str(application_id),
                        "document_id": str(document_id),
                    },
                )
    return _document_response_from_joined(row)


async def prepare_document_download(
    pool: AsyncConnectionPool,
    *,
    application_id: UUID,
    document_id: UUID,
    actor_user_id: UUID,
    request_id: str | None = None,
) -> DocumentContent:
    """
    Authorize download internals: membership check, clean-scan gate, audit append.

    Returns a filesystem path for the router to stream. Never returns storage_uri.
    """
    async with pool.connection() as conn:
        async with conn.transaction():
            row = await repository.fetch_document_with_file(
                conn,
                application_id=application_id,
                document_id=document_id,
            )
            if row is None:
                raise AppError(
                    code="DOCUMENT_NOT_FOUND",
                    message="Document not found for this application.",
                    status_code=404,
                    details={
                        "application_id": str(application_id),
                        "document_id": str(document_id),
                    },
                )
            if row["malware_scanned_at"] is None:
                raise AppError(
                    code="DOCUMENT_NOT_SCANNED",
                    message="Document content is not available until malware scan completes.",
                    status_code=409,
                    details={
                        "application_id": str(application_id),
                        "document_id": str(document_id),
                    },
                )

            path = _path_from_storage_uri(row["storage_uri"])
            if not path.is_file():
                raise AppError(
                    code="DOCUMENT_CONTENT_MISSING",
                    message="Stored file bytes are missing from controlled storage.",
                    status_code=500,
                    details={
                        "application_id": str(application_id),
                        "document_id": str(document_id),
                    },
                )

            await append_audit_event(
                conn,
                action="document.download",
                actor_type="user",
                actor_user_id=actor_user_id,
                application_id=application_id,
                entity_type="application_documents",
                entity_id=document_id,
                after_json={
                    "id": str(document_id),
                    "application_id": str(application_id),
                    "content_sha256": row["content_sha256"],
                    "original_filename": row["original_filename"],
                    "mime_type": row["mime_type"],
                    "byte_size": row["byte_size"],
                },
                request_id=request_id,
            )

    return DocumentContent(
        document_id=document_id,
        application_id=application_id,
        path=path,
        mime_type=row["mime_type"],
        original_filename=row["original_filename"],
        byte_size=int(row["byte_size"]),
    )


async def upload_document(
    pool: AsyncConnectionPool,
    *,
    application_id: UUID,
    actor_user_id: UUID,
    actor_role: ActorRole,
    filename: str,
    declared_mime: str,
    data: bytes,
    document_type: DocumentType,
    scanner: MalwareScanner,
    settings: Settings | None = None,
    request_id: str | None = None,
) -> DocumentResponse:
    """
    Quarantine → scan → clean store → persist stored_files + application_documents.

    Infected bytes and scanner errors never insert document rows (ADR-025).
    """
    cfg = settings or get_settings()

    if actor_role not in _UPLOAD_ALLOWED_ROLES:
        raise AppError(
            code="AUTH_FORBIDDEN",
            message="Reviewers cannot upload documents.",
            status_code=403,
            details={"role": actor_role},
        )

    if len(data) > cfg.MAX_UPLOAD_SIZE_BYTES:
        raise AppError(
            code="DOCUMENT_TOO_LARGE",
            message="Upload exceeds the maximum allowed size.",
            status_code=413,
            details={
                "max_upload_size_bytes": cfg.MAX_UPLOAD_SIZE_BYTES,
                "byte_size": len(data),
            },
        )
    if len(data) == 0:
        raise AppError(
            code="DOCUMENT_EMPTY",
            message="Upload must contain at least one byte.",
            status_code=422,
        )

    try:
        _kind, normalized_mime = validate_file_signature(
            filename=filename,
            declared_mime=declared_mime,
            data=data,
        )
    except ValueError as exc:
        raise AppError(
            code="DOCUMENT_SIGNATURE_INVALID",
            message=str(exc),
            status_code=422,
            details={"filename": filename, "declared_mime": declared_mime},
        ) from exc

    content_sha256 = hashlib.sha256(data).hexdigest()
    quarantine_dir = Path(cfg.QUARANTINE_DIR)
    clean_dir = Path(cfg.CLEAN_UPLOADS_DIR)
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    clean_dir.mkdir(parents=True, exist_ok=True)

    quarantine_path = quarantine_dir / f"{uuid4().hex}.bin"
    quarantine_path.write_bytes(data)

    try:
        scan = await asyncio.to_thread(scanner.scan_bytes, data)
        if scan.status is ScanStatus.INFECTED:
            _purge_path(quarantine_path)
            async with pool.connection() as conn:
                async with conn.transaction():
                    await append_audit_event(
                        conn,
                        action="document.upload_rejected",
                        actor_type="user",
                        actor_user_id=actor_user_id,
                        application_id=application_id,
                        entity_type="application",
                        entity_id=application_id,
                        rationale="malware_infected",
                        after_json={
                            "reason": "infected",
                            "threat_name": scan.threat_name,
                            "content_sha256": content_sha256,
                            "original_filename": filename,
                        },
                        request_id=request_id,
                    )
            raise AppError(
                code="DOCUMENT_INFECTED",
                message="Upload rejected: malware detected.",
                status_code=422,
                details={"threat_name": scan.threat_name},
            )
        if scan.status is ScanStatus.ERROR:
            _purge_path(quarantine_path)
            async with pool.connection() as conn:
                async with conn.transaction():
                    await append_audit_event(
                        conn,
                        action="document.upload_rejected",
                        actor_type="user",
                        actor_user_id=actor_user_id,
                        application_id=application_id,
                        entity_type="application",
                        entity_id=application_id,
                        rationale="scanner_error",
                        after_json={
                            "reason": "scanner_error",
                            "detail": scan.detail,
                            "content_sha256": content_sha256,
                            "original_filename": filename,
                        },
                        request_id=request_id,
                    )
            raise AppError(
                code="DOCUMENT_SCAN_FAILED",
                message="Upload rejected: malware scanner unavailable or failed.",
                status_code=503,
                details={"detail": scan.detail},
            )

        now = _utcnow()
        clean_path = clean_dir / content_sha256
        storage_uri = clean_path.resolve().as_uri()

        async with pool.connection() as conn:
            async with conn.transaction():
                application = await repository.lock_application_for_update(
                    conn, application_id
                )
                if application is None:
                    _purge_path(quarantine_path)
                    raise AppError(
                        code="APPLICATION_NOT_FOUND",
                        message="Application not found.",
                        status_code=404,
                        details={"application_id": str(application_id)},
                    )
                if application["status"] not in _UPLOAD_ALLOWED_STATUSES:
                    _purge_path(quarantine_path)
                    raise AppError(
                        code="APPLICATION_INVALID_STATE",
                        message=(
                            "Documents can only be uploaded when the application "
                            "is draft, clarification_required, or processing_failed."
                        ),
                        status_code=409,
                        details={
                            "application_id": str(application_id),
                            "status": application["status"],
                            "allowed_statuses": sorted(_UPLOAD_ALLOWED_STATUSES),
                        },
                    )

                existing_doc = await repository.fetch_active_document_by_hash(
                    conn,
                    application_id=application_id,
                    content_sha256=content_sha256,
                )
                if existing_doc is not None:
                    _purge_path(quarantine_path)
                    raise AppError(
                        code="DOCUMENT_DUPLICATE",
                        message=(
                            "An active document with the same content already "
                            "exists on this application."
                        ),
                        status_code=409,
                        details={
                            "application_id": str(application_id),
                            "content_sha256": content_sha256,
                            "existing_document_id": str(existing_doc["id"]),
                        },
                    )

                if not clean_path.exists():
                    shutil.move(str(quarantine_path), str(clean_path))
                else:
                    _purge_path(quarantine_path)

                stored_file = await repository.insert_stored_file_if_absent(
                    conn,
                    content_sha256=content_sha256,
                    storage_uri=storage_uri,
                    original_filename=filename,
                    mime_type=normalized_mime,
                    byte_size=len(data),
                    signature_ok=True,
                    malware_scanner=scan.scanner_name,
                    malware_scanned_at=now,
                    malware_signature_version=scan.signature_version,
                    created_at=now,
                )

                try:
                    document = await repository.insert_application_document(
                        conn,
                        application_id=application_id,
                        stored_file_id=stored_file["id"],
                        content_sha256=content_sha256,
                        document_type=document_type,
                        uploaded_by_user_id=actor_user_id,
                        created_at=now,
                        updated_at=now,
                    )
                except UniqueViolation as exc:
                    raise AppError(
                        code="DOCUMENT_DUPLICATE",
                        message=(
                            "An active document with the same content already "
                            "exists on this application."
                        ),
                        status_code=409,
                        details={
                            "application_id": str(application_id),
                            "content_sha256": content_sha256,
                        },
                    ) from exc

                await append_audit_event(
                    conn,
                    action="document.upload",
                    actor_type="user",
                    actor_user_id=actor_user_id,
                    application_id=application_id,
                    entity_type="application_document",
                    entity_id=document["id"],
                    after_json={
                        "id": str(document["id"]),
                        "document_type": document["document_type"],
                        "content_sha256": content_sha256,
                        "byte_size": len(data),
                        "mime_type": normalized_mime,
                        "original_filename": filename,
                    },
                    request_id=request_id,
                )

        return _document_response(document, stored_file)
    except AppError:
        raise
    except Exception:
        _purge_path(quarantine_path)
        raise


__all__ = [
    "DocumentContent",
    "archive_application",
    "create_draft_application",
    "get_application",
    "get_document",
    "list_applications",
    "list_documents",
    "prepare_document_download",
    "submit_application",
    "update_draft_application",
    "upload_document",
    "withdraw_application",
]
