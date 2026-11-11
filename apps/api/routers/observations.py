"""AI observations query routes (M11; BR-12 advisory, read-only)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from psycopg_pool import AsyncConnectionPool

from modules.identity.deps import get_pool
from modules.identity.rbac import require_role
from modules.identity.schemas import UserProfile
from modules.observations.schemas import AiObservationResponse
from modules.observations.service import fetch_latest_ai_observation
from shared.errors import AppError

router = APIRouter(
    prefix="/api/v1/applications/{application_id}/observations",
    tags=["observations"],
)

require_observations_reader = require_role("loan_officer", "reviewer", "admin")


@router.get("", response_model=AiObservationResponse)
async def get_latest_observation_endpoint(
    application_id: UUID,
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    _user: Annotated[UserProfile, Depends(require_observations_reader)],
) -> AiObservationResponse:
    """
    Return the latest labelled AI observation for the application.

    Response always carries ``is_ai_generated: true`` (advisory only).
    """
    async with pool.connection() as conn:
        observation = await fetch_latest_ai_observation(conn, application_id)
    if observation is None:
        raise AppError(
            code="AI_OBSERVATION_NOT_FOUND",
            message="No AI observation found for this application.",
            status_code=404,
            details={"application_id": str(application_id)},
        )
    return observation
