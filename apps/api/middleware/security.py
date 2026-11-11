"""Production security headers and CORS origin hardening (Architecture §12)."""

from __future__ import annotations

from collections.abc import Sequence

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "1; mode=block",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Content-Security-Policy": "default-src 'self'",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Inject browser security headers on every response."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        response = await call_next(request)
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response


def configure_security_middleware(
    app: FastAPI,
    *,
    allowed_origins: Sequence[str],
) -> None:
    """
    Attach security headers and strict CORS.

    Origins must be an explicit allow-list. An empty list disables cross-origin
    credentialed access (no wildcard ``*`` with credentials).
    """
    origins = [origin.strip() for origin in allowed_origins if origin.strip()]
    # Never allow credentials with a wildcard origin.
    if "*" in origins:
        raise ValueError(
            "CORS_ALLOWED_ORIGINS must not include '*' when credentials are enabled"
        )

    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-CSRF-Token",
            "X-Request-ID",
        ],
        expose_headers=["X-Request-ID"],
    )
