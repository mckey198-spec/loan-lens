"""Session secret hashing and CSRF derivation (ADR-019, Database Design §4.1)."""

from __future__ import annotations

import hashlib
import hmac
import secrets


def generate_session_secret() -> str:
    """Create a 32-byte URL-safe session cookie secret."""
    return secrets.token_urlsafe(32)


def hash_session_secret(raw_secret: str) -> str:
    """SHA-256 hex digest of the cookie secret for user_sessions.token_hash."""
    return hashlib.sha256(raw_secret.encode("utf-8")).hexdigest()


def csrf_token_for_session(raw_secret: str) -> str:
    """Derive the CSRF secret from the raw session cookie (never stored)."""
    return hmac.new(
        key=raw_secret.encode("utf-8"),
        msg=b"loanlens-csrf-v1",
        digestmod=hashlib.sha256,
    ).hexdigest()


def csrf_tokens_match(expected: str, provided: str | None) -> bool:
    if provided is None:
        return False
    return hmac.compare_digest(expected, provided)
