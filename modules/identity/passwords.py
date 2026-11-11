"""Argon2id password hashing (ADR-019)."""

from __future__ import annotations

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from shared.settings import Settings, get_settings


def _hasher(settings: Settings | None = None) -> PasswordHasher:
    cfg = settings or get_settings()
    return PasswordHasher(
        time_cost=cfg.ARGON2_TIME_COST,
        memory_cost=cfg.ARGON2_MEMORY_COST,
        parallelism=cfg.ARGON2_PARALLELISM,
        type=Type.ID,
    )


def hash_password(password: str, *, settings: Settings | None = None) -> str:
    """Return an Argon2id hash suitable for users.password_hash."""
    return _hasher(settings).hash(password)


def verify_password(
    password_hash: str,
    password: str,
    *,
    settings: Settings | None = None,
) -> bool:
    """Return True when password matches the stored Argon2id hash."""
    try:
        return _hasher(settings).verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
