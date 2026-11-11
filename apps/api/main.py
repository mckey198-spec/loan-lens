"""LoanLens API process entrypoint."""

from __future__ import annotations

import asyncio
import sys
import uuid
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request, Response

# psycopg async cannot use ProactorEventLoop (default on Windows).
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from apps.api.metrics import router as metrics_router
from apps.api.middleware.security import configure_security_middleware
from apps.api.routers import applications as applications_router
from apps.api.routers import assessments as assessments_router
from apps.api.routers import audit as audit_router
from apps.api.routers import auth as auth_router
from apps.api.routers import calculations as calculations_router
from apps.api.routers import documents as documents_router
from apps.api.routers import observations as observations_router
from apps.api.routers import policies as policies_router
from apps.api.routers import reporting as reporting_router
from apps.api.routers import users as users_router
from apps.api.routers import verification as verification_router
from shared.db import create_pool
from shared.errors import AppError, app_error_handler
from shared.logging import setup_logging
from shared.settings import get_settings

settings = get_settings()
setup_logging(log_level="DEBUG" if settings.DEBUG else "INFO")

APP_VERSION = "1.0.0"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    pool = create_pool(get_settings().DATABASE_URL)
    await pool.open()
    app.state.pool = pool
    try:
        yield
    finally:
        await pool.close()
        app.state.pool = None


app = FastAPI(title="LoanLens API", version=APP_VERSION, lifespan=lifespan)
configure_security_middleware(
    app,
    allowed_origins=settings.cors_allowed_origins_list(),
)
app.add_exception_handler(AppError, app_error_handler)
app.include_router(auth_router.router)
app.include_router(users_router.router)
app.include_router(applications_router.router)
app.include_router(assessments_router.router)
app.include_router(documents_router.router)
app.include_router(policies_router.router)
app.include_router(policies_router.application_citations_router)
app.include_router(verification_router.router)
app.include_router(calculations_router.router)
app.include_router(observations_router.router)
app.include_router(reporting_router.router)
app.include_router(audit_router.router)
app.include_router(metrics_router)


@app.middleware("http")
async def attach_request_id(request: Request, call_next) -> Response:  # noqa: ANN001
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.get("/health")
def health() -> dict[str, str]:
    """Process liveness. Does not open a SQL connection (M01-T05)."""
    return {
        "status": "ok",
        "app_env": settings.APP_ENV,
        "version": APP_VERSION,
    }
