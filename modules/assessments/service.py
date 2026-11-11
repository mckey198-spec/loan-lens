"""Credit reviewer claim, disposition, correction, and decision workflow (M12-T03)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg.errors import CheckViolation, InsufficientPrivilege, UniqueViolation

from apps.api.metrics import (
    record_application_status,
    record_assessment_recommendation,
)
from modules.assessments import repository
from modules.assessments.schemas import (
    AssessmentListResponse,
    AssessmentRecommendation,
    AssessmentResponse,
    FieldCorrectionResponse,
    FindingDispositionResponse,
)
from modules.governance.audit import append_audit_event
from shared.errors import AppError

_MIN_JUSTIFICATION_LEN = 10

_RECOMMENDATION_TO_STATUS: dict[str, str] = {
    "approve": "review_completed",
    "exception_approved": "review_completed",
    "reject": "review_completed",
    "refer": "escalated",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _require_min_text(value: str, *, field_name: str) -> str:
    stripped = value.strip()
    if len(stripped) < _MIN_JUSTIFICATION_LEN:
        raise AppError(
            code="VALIDATION_ERROR",
            message=(
                f"{field_name} must be at least {_MIN_JUSTIFICATION_LEN} "
                "characters after trimming."
            ),
            status_code=422,
            details={
                "field": field_name,
                "minimum_length": _MIN_JUSTIFICATION_LEN,
            },
        )
    return stripped


def _assessment_response(row: dict[str, Any]) -> AssessmentResponse:
    return AssessmentResponse(
        id=row["id"],
        application_id=row["application_id"],
        processing_run_id=row["processing_run_id"],
        input_snapshot_id=row["input_snapshot_id"],
        applicable_policy_version_id=row["applicable_policy_version_id"],
        assigned_reviewer_id=row["assigned_reviewer_id"],
        recommendation=row["recommendation"],
        reviewer_notes=row["reviewer_notes"],
        decided_at=row["decided_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _disposition_response(row: dict[str, Any]) -> FindingDispositionResponse:
    return FindingDispositionResponse(
        id=row["id"],
        application_id=row["application_id"],
        assessment_id=row["assessment_id"],
        verification_finding_id=row["verification_finding_id"],
        action=row["action"],
        justification=row["justification"],
        created_by_user_id=row["created_by_user_id"],
        created_at=row["created_at"],
    )


def _correction_response(row: dict[str, Any]) -> FieldCorrectionResponse:
    return FieldCorrectionResponse(
        id=row["id"],
        application_id=row["application_id"],
        extracted_field_id=row["extracted_field_id"],
        assessment_id=row["assessment_id"],
        corrected_value_text=row["corrected_value_text"],
        corrected_value_numeric=row["corrected_value_numeric"],
        corrected_value_date=row["corrected_value_date"],
        reason=row["reason"],
        corrected_by_user_id=row["corrected_by_user_id"],
        created_at=row["created_at"],
    )


def _raise_if_missing_application(
    application: dict[str, Any] | None,
    application_id: UUID,
) -> dict[str, Any]:
    if application is None:
        raise AppError(
            code="APPLICATION_NOT_FOUND",
            message="Application not found.",
            status_code=404,
            details={"application_id": str(application_id)},
        )
    return application


def _require_status(
    application: dict[str, Any],
    *,
    expected: str,
    application_id: UUID,
) -> None:
    if application["status"] != expected:
        raise AppError(
            code="APPLICATION_INVALID_STATE",
            message=(
                f"Application must be in '{expected}' status "
                "for this review action."
            ),
            status_code=409,
            details={
                "application_id": str(application_id),
                "status": application["status"],
                "expected_status": expected,
            },
        )


def _require_open_assessment(assessment: dict[str, Any]) -> None:
    if assessment["decided_at"] is not None:
        raise AppError(
            code="ASSESSMENT_ALREADY_DECIDED",
            message="Decided assessments cannot be modified.",
            status_code=409,
            details={
                "assessment_id": str(assessment["id"]),
                "decided_at": assessment["decided_at"].isoformat(),
            },
        )


async def _load_open_assessment(
    connection: Any,
    *,
    application_id: UUID,
    assessment_id: UUID,
) -> dict[str, Any]:
    assessment = await repository.fetch_assessment(
        connection,
        application_id=application_id,
        assessment_id=assessment_id,
        for_update=True,
    )
    if assessment is None:
        raise AppError(
            code="ASSESSMENT_NOT_FOUND",
            message="Assessment not found for this application.",
            status_code=404,
            details={
                "application_id": str(application_id),
                "assessment_id": str(assessment_id),
            },
        )
    _require_open_assessment(assessment)
    return assessment


async def claim_application_for_review(
    connection: Any,
    application_id: UUID,
    reviewer_user_id: UUID,
    *,
    request_id: str | None = None,
) -> AssessmentResponse:
    """
    Claim an application awaiting review: in_review + assessment + audit.

    Status update, state history, assessment insert, and audit event run in
    the caller's open transaction.
    """
    now = _utcnow()
    application = _raise_if_missing_application(
        await repository.lock_application(connection, application_id),
        application_id,
    )
    _require_status(
        application,
        expected="awaiting_human_review",
        application_id=application_id,
    )

    run = await repository.fetch_latest_succeeded_run(connection, application_id)
    if run is None:
        raise AppError(
            code="PROCESSING_RUN_NOT_FOUND",
            message=(
                "No succeeded processing run with an input snapshot "
                "is available to claim."
            ),
            status_code=409,
            details={"application_id": str(application_id)},
        )

    policy_version_id = run["applicable_policy_version_id"]
    if policy_version_id is None:
        policy_version_id = application["applicable_policy_version_id"]
    if policy_version_id is None:
        raise AppError(
            code="POLICY_VERSION_REQUIRED",
            message="Application has no applicable policy version for assessment.",
            status_code=409,
            details={"application_id": str(application_id)},
        )

    await repository.insert_state_history(
        connection,
        application_id=application_id,
        from_status="awaiting_human_review",
        to_status="in_review",
        actor_user_id=reviewer_user_id,
        reason="Reviewer claimed application for human review",
        created_at=now,
    )
    await repository.transition_application_status(
        connection,
        application_id=application_id,
        to_status="in_review",
        assigned_reviewer_user_id=reviewer_user_id,
        updated_at=now,
    )

    try:
        assessment = await repository.insert_assessment(
            connection,
            application_id=application_id,
            processing_run_id=run["processing_run_id"],
            input_snapshot_id=run["input_snapshot_id"],
            applicable_policy_version_id=policy_version_id,
            assigned_reviewer_id=reviewer_user_id,
            created_at=now,
        )
    except UniqueViolation as exc:
        raise AppError(
            code="ASSESSMENT_ALREADY_EXISTS",
            message="An assessment already exists for the latest processing run.",
            status_code=409,
            details={
                "application_id": str(application_id),
                "processing_run_id": str(run["processing_run_id"]),
            },
        ) from exc

    await append_audit_event(
        connection,
        action="application.claimed",
        actor_type="user",
        actor_user_id=reviewer_user_id,
        application_id=application_id,
        entity_type="assessment",
        entity_id=assessment["id"],
        assessment_id=assessment["id"],
        after_json={
            "application_id": str(application_id),
            "assessment_id": str(assessment["id"]),
            "from_status": "awaiting_human_review",
            "to_status": "in_review",
            "assigned_reviewer_id": str(reviewer_user_id),
        },
        request_id=request_id,
    )
    record_application_status("in_review")
    return _assessment_response(assessment)


async def record_finding_disposition(
    connection: Any,
    application_id: UUID,
    assessment_id: UUID,
    finding_id: UUID,
    action: str,
    justification: str,
    reviewer_user_id: UUID,
    *,
    request_id: str | None = None,
) -> FindingDispositionResponse:
    """Append a finding disposition while the application is in_review."""
    justified = _require_min_text(justification, field_name="justification")

    application = _raise_if_missing_application(
        await repository.lock_application(connection, application_id),
        application_id,
    )
    _require_status(
        application,
        expected="in_review",
        application_id=application_id,
    )
    await _load_open_assessment(
        connection,
        application_id=application_id,
        assessment_id=assessment_id,
    )

    finding = await repository.fetch_verification_finding(
        connection,
        application_id=application_id,
        finding_id=finding_id,
    )
    if finding is None:
        raise AppError(
            code="FINDING_NOT_FOUND",
            message="Verification finding not found for this application.",
            status_code=404,
            details={
                "application_id": str(application_id),
                "finding_id": str(finding_id),
            },
        )

    try:
        disposition = await repository.insert_finding_disposition(
            connection,
            application_id=application_id,
            assessment_id=assessment_id,
            verification_finding_id=finding_id,
            action=action,
            justification=justified,
            created_by_user_id=reviewer_user_id,
        )
    except CheckViolation as exc:
        raise AppError(
            code="VALIDATION_ERROR",
            message="Finding disposition failed database validation.",
            status_code=422,
            details={"application_id": str(application_id)},
        ) from exc
    except UniqueViolation as exc:
        raise AppError(
            code="FINDING_DISPOSITION_EXISTS",
            message="A disposition already exists for this finding on the assessment.",
            status_code=409,
            details={
                "assessment_id": str(assessment_id),
                "finding_id": str(finding_id),
            },
        ) from exc

    await append_audit_event(
        connection,
        action="finding.disposition",
        actor_type="user",
        actor_user_id=reviewer_user_id,
        application_id=application_id,
        entity_type="finding_disposition",
        entity_id=disposition["id"],
        assessment_id=assessment_id,
        rationale=justified,
        after_json={
            "disposition_id": str(disposition["id"]),
            "finding_id": str(finding_id),
            "action": action,
        },
        request_id=request_id,
    )
    return _disposition_response(disposition)


async def record_field_correction(
    connection: Any,
    application_id: UUID,
    assessment_id: UUID,
    extracted_field_id: UUID,
    corrected_values: dict[str, Any],
    reason: str,
    reviewer_user_id: UUID,
    *,
    request_id: str | None = None,
) -> FieldCorrectionResponse:
    """Append a typed field correction while the application is in_review."""
    corrected_reason = _require_min_text(reason, field_name="reason")

    text_raw = corrected_values.get("corrected_value_text")
    text_value: str | None
    if isinstance(text_raw, str):
        text_value = text_raw.strip() or None
    else:
        text_value = None

    numeric_raw = corrected_values.get("corrected_value_numeric")
    numeric_value: Decimal | None
    if isinstance(numeric_raw, Decimal):
        numeric_value = numeric_raw
    elif numeric_raw is None:
        numeric_value = None
    else:
        numeric_value = Decimal(str(numeric_raw))

    date_raw = corrected_values.get("corrected_value_date")
    date_value: date | None
    if isinstance(date_raw, date):
        date_value = date_raw
    elif isinstance(date_raw, str) and date_raw.strip():
        date_value = date.fromisoformat(date_raw.strip()[:10])
    else:
        date_value = None

    if text_value is None and numeric_value is None and date_value is None:
        raise AppError(
            code="VALIDATION_ERROR",
            message=(
                "At least one corrected value "
                "(text, numeric, or date) is required."
            ),
            status_code=422,
            details={"extracted_field_id": str(extracted_field_id)},
        )

    application = _raise_if_missing_application(
        await repository.lock_application(connection, application_id),
        application_id,
    )
    _require_status(
        application,
        expected="in_review",
        application_id=application_id,
    )
    await _load_open_assessment(
        connection,
        application_id=application_id,
        assessment_id=assessment_id,
    )

    field = await repository.fetch_extracted_field(
        connection,
        application_id=application_id,
        extracted_field_id=extracted_field_id,
    )
    if field is None:
        raise AppError(
            code="EXTRACTED_FIELD_NOT_FOUND",
            message="Extracted field not found for this application.",
            status_code=404,
            details={
                "application_id": str(application_id),
                "extracted_field_id": str(extracted_field_id),
            },
        )

    try:
        correction = await repository.insert_field_correction(
            connection,
            application_id=application_id,
            assessment_id=assessment_id,
            extracted_field_id=extracted_field_id,
            corrected_value_text=text_value,
            corrected_value_numeric=numeric_value,
            corrected_value_date=date_value,
            reason=corrected_reason,
            corrected_by_user_id=reviewer_user_id,
        )
    except CheckViolation as exc:
        raise AppError(
            code="VALIDATION_ERROR",
            message="Field correction failed database validation.",
            status_code=422,
            details={"extracted_field_id": str(extracted_field_id)},
        ) from exc

    await append_audit_event(
        connection,
        action="field.corrected",
        actor_type="user",
        actor_user_id=reviewer_user_id,
        application_id=application_id,
        entity_type="field_correction",
        entity_id=correction["id"],
        assessment_id=assessment_id,
        rationale=corrected_reason,
        after_json={
            "correction_id": str(correction["id"]),
            "extracted_field_id": str(extracted_field_id),
            "has_text": correction["corrected_value_text"] is not None,
            "has_numeric": correction["corrected_value_numeric"] is not None,
            "has_date": correction["corrected_value_date"] is not None,
        },
        request_id=request_id,
    )
    return _correction_response(correction)


async def decide_assessment(
    connection: Any,
    application_id: UUID,
    assessment_id: UUID,
    recommendation: str,
    reviewer_notes: str,
    reviewer_user_id: UUID,
    *,
    request_id: str | None = None,
) -> AssessmentResponse:
    """
    Record the assessment recommendation and transition application status.

    Mapping (ten-state machine):
    - approve / exception_approved / reject → review_completed
    - refer → escalated
    """
    notes = _require_min_text(reviewer_notes, field_name="reviewer_notes")
    if recommendation not in _RECOMMENDATION_TO_STATUS:
        raise AppError(
            code="VALIDATION_ERROR",
            message="Invalid assessment recommendation.",
            status_code=422,
            details={"recommendation": recommendation},
        )
    to_status = _RECOMMENDATION_TO_STATUS[recommendation]
    now = _utcnow()

    application = _raise_if_missing_application(
        await repository.lock_application(connection, application_id),
        application_id,
    )
    _require_status(
        application,
        expected="in_review",
        application_id=application_id,
    )
    assessment = await _load_open_assessment(
        connection,
        application_id=application_id,
        assessment_id=assessment_id,
    )

    await repository.insert_state_history(
        connection,
        application_id=application_id,
        from_status="in_review",
        to_status=to_status,
        actor_user_id=reviewer_user_id,
        reason=f"Assessment decided: {recommendation}",
        created_at=now,
    )
    await repository.transition_application_status(
        connection,
        application_id=application_id,
        to_status=to_status,
        assigned_reviewer_user_id=None,
        updated_at=now,
    )

    try:
        decided = await repository.decide_assessment(
            connection,
            assessment_id=assessment_id,
            recommendation=recommendation,
            reviewer_notes=notes,
            decided_at=now,
        )
    except InsufficientPrivilege as exc:
        raise AppError(
            code="ASSESSMENT_ALREADY_DECIDED",
            message="Decided assessments cannot be modified.",
            status_code=409,
            details={"assessment_id": str(assessment_id)},
        ) from exc

    if decided is None:
        raise AppError(
            code="ASSESSMENT_NOT_FOUND",
            message="Assessment not found for this application.",
            status_code=404,
            details={
                "application_id": str(application_id),
                "assessment_id": str(assessment_id),
            },
        )

    await append_audit_event(
        connection,
        action="assessment.decided",
        actor_type="user",
        actor_user_id=reviewer_user_id,
        application_id=application_id,
        entity_type="assessment",
        entity_id=assessment_id,
        assessment_id=assessment_id,
        rationale=notes,
        before_json={
            "recommendation": assessment["recommendation"],
            "decided_at": None,
            "application_status": "in_review",
        },
        after_json={
            "recommendation": recommendation,
            "decided_at": now.isoformat(),
            "application_status": to_status,
        },
        request_id=request_id,
    )
    record_assessment_recommendation(recommendation)
    record_application_status(to_status)
    return _assessment_response(decided)


async def list_assessments_for_application(
    connection: Any,
    application_id: UUID,
) -> AssessmentListResponse:
    """Return assessments for an application (newest first)."""
    application = await repository.fetch_application(connection, application_id)
    if application is None:
        raise AppError(
            code="APPLICATION_NOT_FOUND",
            message="Application not found.",
            status_code=404,
            details={"application_id": str(application_id)},
        )
    rows = await repository.list_assessments(connection, application_id)
    items = [_assessment_response(row) for row in rows]
    return AssessmentListResponse(items=items, total=len(items))


def recommendation_terminal_status(
    recommendation: AssessmentRecommendation | str,
) -> str:
    """Expose recommendation → application status mapping for tests/callers."""
    return _RECOMMENDATION_TO_STATUS[str(recommendation)]
