"""Credit reviewer assessment routes (PRD §11; M12-T03)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from psycopg_pool import AsyncConnectionPool

from modules.assessments.schemas import (
    AssessmentDecisionRequest,
    AssessmentListResponse,
    AssessmentResponse,
    FieldCorrectionRequest,
    FieldCorrectionResponse,
    FindingDispositionRequest,
    FindingDispositionResponse,
)
from modules.assessments.service import (
    claim_application_for_review,
    decide_assessment,
    list_assessments_for_application,
    record_field_correction,
    record_finding_disposition,
)
from modules.identity.deps import get_pool
from modules.identity.rbac import require_role
from modules.identity.schemas import UserProfile
from shared.errors import request_id_from_request

router = APIRouter(prefix="/api/v1/applications", tags=["assessments"])

# Maker-checker: Loan Officers cannot claim, dispose, correct, or decide.
require_reviewer_or_admin = require_role("reviewer", "admin")
# Review workspace reads remain available to officers (view-only).
require_assessment_reader = require_role("loan_officer", "reviewer", "admin")


@router.post(
    "/{application_id}/claim",
    response_model=AssessmentResponse,
    status_code=201,
)
async def claim_application_endpoint(
    application_id: UUID,
    request: Request,
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    actor: Annotated[UserProfile, Depends(require_reviewer_or_admin)],
) -> AssessmentResponse:
    async with pool.connection() as conn:
        async with conn.transaction():
            return await claim_application_for_review(
                conn,
                application_id,
                actor.id,
                request_id=request_id_from_request(request),
            )


@router.get(
    "/{application_id}/assessments",
    response_model=AssessmentListResponse,
)
async def list_assessments_endpoint(
    application_id: UUID,
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    _user: Annotated[UserProfile, Depends(require_assessment_reader)],
) -> AssessmentListResponse:
    async with pool.connection() as conn:
        return await list_assessments_for_application(conn, application_id)


@router.post(
    "/{application_id}/assessments/{assessment_id}/dispositions",
    response_model=FindingDispositionResponse,
    status_code=201,
)
async def record_disposition_endpoint(
    application_id: UUID,
    assessment_id: UUID,
    body: FindingDispositionRequest,
    request: Request,
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    actor: Annotated[UserProfile, Depends(require_reviewer_or_admin)],
) -> FindingDispositionResponse:
    async with pool.connection() as conn:
        async with conn.transaction():
            return await record_finding_disposition(
                conn,
                application_id,
                assessment_id,
                body.finding_id,
                body.action,
                body.justification,
                actor.id,
                request_id=request_id_from_request(request),
            )


@router.post(
    "/{application_id}/assessments/{assessment_id}/corrections",
    response_model=FieldCorrectionResponse,
    status_code=201,
)
async def record_correction_endpoint(
    application_id: UUID,
    assessment_id: UUID,
    body: FieldCorrectionRequest,
    request: Request,
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    actor: Annotated[UserProfile, Depends(require_reviewer_or_admin)],
) -> FieldCorrectionResponse:
    async with pool.connection() as conn:
        async with conn.transaction():
            return await record_field_correction(
                conn,
                application_id,
                assessment_id,
                body.extracted_field_id,
                {
                    "corrected_value_text": body.corrected_value_text,
                    "corrected_value_numeric": body.corrected_value_numeric,
                    "corrected_value_date": body.corrected_value_date,
                },
                body.reason,
                actor.id,
                request_id=request_id_from_request(request),
            )


@router.post(
    "/{application_id}/assessments/{assessment_id}/decide",
    response_model=AssessmentResponse,
)
async def decide_assessment_endpoint(
    application_id: UUID,
    assessment_id: UUID,
    body: AssessmentDecisionRequest,
    request: Request,
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    actor: Annotated[UserProfile, Depends(require_reviewer_or_admin)],
) -> AssessmentResponse:
    async with pool.connection() as conn:
        async with conn.transaction():
            return await decide_assessment(
                conn,
                application_id,
                assessment_id,
                body.recommendation,
                body.reviewer_notes,
                actor.id,
                request_id=request_id_from_request(request),
            )
