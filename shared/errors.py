"""Structured API errors (Architecture §11)."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str


class AppError(Exception):
    """Application error raised by services and translated to a structured body."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


def request_id_from_request(request: Request) -> str:
    """Prefer inbound X-Request-ID; otherwise use the value set by middleware."""
    existing = getattr(request.state, "request_id", None)
    if isinstance(existing, str) and existing:
        return existing
    header = request.headers.get("x-request-id")
    if header:
        return header
    return "unknown"


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    body = ErrorBody(
        code=exc.code,
        message=exc.message,
        details=exc.details,
        request_id=request_id_from_request(request),
    )
    return JSONResponse(status_code=exc.status_code, content=body.model_dump())
