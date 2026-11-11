"""Authentication, session lifecycle, and Admin user management (ADR-019)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.errors import UniqueViolation
from psycopg_pool import AsyncConnectionPool

from modules.governance.audit import append_audit_event
from modules.identity import repository
from modules.identity.passwords import hash_password, verify_password
from modules.identity.schemas import (
    APPROVED_ROLES,
    AuthenticatedPrincipal,
    LoginResponse,
    UserListResponse,
    UserProfile,
    UserRole,
)
from modules.identity.tokens import (
    csrf_token_for_session,
    csrf_tokens_match,
    generate_session_secret,
    hash_session_secret,
)
from shared.errors import AppError
from shared.settings import Settings, get_settings

_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _profile_from_row(row: dict[str, Any]) -> UserProfile:
    return UserProfile(
        id=row["id"] if "id" in row else row["user_id"],
        email=row["email"],
        full_name=row["full_name"],
        role=row["role"],
        is_active=row["is_active"],
    )


async def login(
    pool: AsyncConnectionPool,
    *,
    email: str,
    password: str,
    prior_session_secret: str | None = None,
    settings: Settings | None = None,
) -> tuple[LoginResponse, str]:
    """
    Verify credentials, rotate the session id, and return (response, raw cookie secret).

    Stores only SHA-256(token) in user_sessions.token_hash.
    """
    cfg = settings or get_settings()
    normalized_email = email.strip().lower()
    now = _utcnow()

    async with pool.connection() as conn:
        async with conn.transaction():
            user = await repository.fetch_user_by_email(conn, normalized_email)
            if user is None or not verify_password(user["password_hash"], password, settings=cfg):
                raise AppError(
                    code="AUTH_INVALID_CREDENTIALS",
                    message="Invalid email or password.",
                    status_code=401,
                )
            if not user["is_active"]:
                raise AppError(
                    code="AUTH_USER_INACTIVE",
                    message="User account is inactive.",
                    status_code=401,
                )

            if prior_session_secret:
                await repository.revoke_session_by_token_hash(
                    conn,
                    token_hash=hash_session_secret(prior_session_secret),
                    revoked_at=now,
                )

            raw_secret = generate_session_secret()
            token_hash = hash_session_secret(raw_secret)
            expires_at = now + timedelta(seconds=cfg.SESSION_ABSOLUTE_TIMEOUT_SECONDS)
            await repository.insert_session(
                conn,
                user_id=user["id"],
                token_hash=token_hash,
                created_at=now,
                expires_at=expires_at,
                last_seen_at=now,
            )

    csrf_token = csrf_token_for_session(raw_secret)
    response = LoginResponse(
        csrf_token=csrf_token,
        user=_profile_from_row(user),
    )
    return response, raw_secret


async def logout(
    pool: AsyncConnectionPool,
    *,
    raw_session_secret: str,
) -> None:
    now = _utcnow()
    async with pool.connection() as conn:
        async with conn.transaction():
            await repository.revoke_session_by_token_hash(
                conn,
                token_hash=hash_session_secret(raw_session_secret),
                revoked_at=now,
            )


async def revoke_sessions_for_user(
    conn: AsyncConnection[Any],
    *,
    user_id: UUID,
    revoked_at: datetime | None = None,
) -> int:
    """Revoke open sessions when a user is deactivated (same transaction)."""
    return await repository.revoke_all_sessions_for_user(
        conn,
        user_id=user_id,
        revoked_at=revoked_at or _utcnow(),
    )


def _assert_approved_role(role: str) -> UserRole:
    if role not in APPROVED_ROLES:
        raise AppError(
            code="USER_ROLE_INVALID",
            message="Role is not an approved MVP role.",
            status_code=422,
            details={"role": role, "allowed": sorted(APPROVED_ROLES)},
        )
    return role  # type: ignore[return-value]


def _user_audit_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "email": row["email"],
        "full_name": row["full_name"],
        "role": row["role"],
        "is_active": row["is_active"],
    }


async def create_user(
    pool: AsyncConnectionPool,
    *,
    actor_user_id: UUID,
    email: str,
    full_name: str,
    role: str,
    temporary_password: str,
    request_id: str | None = None,
    settings: Settings | None = None,
) -> UserProfile:
    """Admin creates a user; appends audit action user.create."""
    cfg = settings or get_settings()
    approved_role = _assert_approved_role(role)
    normalized_email = email.strip().lower()
    now = _utcnow()
    password_hash = hash_password(temporary_password, settings=cfg)

    async with pool.connection() as conn:
        async with conn.transaction():
            try:
                row = await repository.insert_user(
                    conn,
                    email=normalized_email,
                    full_name=full_name.strip(),
                    role=approved_role,
                    password_hash=password_hash,
                    is_active=True,
                    created_at=now,
                    updated_at=now,
                )
            except UniqueViolation as exc:
                raise AppError(
                    code="USER_EMAIL_EXISTS",
                    message="A user with this email already exists.",
                    status_code=409,
                    details={"email": normalized_email},
                ) from exc

            await append_audit_event(
                conn,
                action="user.create",
                actor_type="user",
                actor_user_id=actor_user_id,
                entity_type="user",
                entity_id=row["id"],
                after_json=_user_audit_snapshot(row),
                request_id=request_id,
            )

    return _profile_from_row(row)


async def list_users(
    pool: AsyncConnectionPool,
    *,
    offset: int,
    limit: int,
) -> UserListResponse:
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

    async with pool.connection() as conn:
        async with conn.transaction():
            rows, total = await repository.list_users(conn, offset=offset, limit=limit)

    return UserListResponse(
        items=[_profile_from_row(row) for row in rows],
        offset=offset,
        limit=limit,
        total=total,
    )


async def get_user(
    pool: AsyncConnectionPool,
    *,
    user_id: UUID,
) -> UserProfile:
    async with pool.connection() as conn:
        async with conn.transaction():
            row = await repository.fetch_user_by_id(conn, user_id)
    if row is None:
        raise AppError(
            code="USER_NOT_FOUND",
            message="User not found.",
            status_code=404,
            details={"user_id": str(user_id)},
        )
    return _profile_from_row(row)


async def update_user(
    pool: AsyncConnectionPool,
    *,
    actor_user_id: UUID,
    user_id: UUID,
    role: str | None = None,
    is_active: bool | None = None,
    request_id: str | None = None,
) -> UserProfile:
    """
    Admin updates role and/or is_active.

    Deactivation (is_active=false) revokes all open sessions in the same transaction.
    Audits user.update_role and/or user.deactivate as applicable.
    """
    if role is None and is_active is None:
        raise AppError(
            code="VALIDATION_ERROR",
            message="At least one of role or is_active must be provided.",
            status_code=422,
        )

    approved_role: UserRole | None = None
    if role is not None:
        approved_role = _assert_approved_role(role)

    now = _utcnow()

    async with pool.connection() as conn:
        async with conn.transaction():
            existing = await repository.fetch_user_by_id(conn, user_id)
            if existing is None:
                raise AppError(
                    code="USER_NOT_FOUND",
                    message="User not found.",
                    status_code=404,
                    details={"user_id": str(user_id)},
                )

            before = _user_audit_snapshot(existing)
            row = await repository.update_user(
                conn,
                user_id=user_id,
                role=approved_role,
                is_active=is_active,
                updated_at=now,
            )
            assert row is not None
            after = _user_audit_snapshot(row)

            if approved_role is not None and approved_role != existing["role"]:
                await append_audit_event(
                    conn,
                    action="user.update_role",
                    actor_type="user",
                    actor_user_id=actor_user_id,
                    entity_type="user",
                    entity_id=user_id,
                    before_json=before,
                    after_json=after,
                    request_id=request_id,
                )

            deactivating = (
                is_active is False and existing["is_active"] is True
            )
            if deactivating:
                await repository.revoke_all_sessions_for_user(
                    conn,
                    user_id=user_id,
                    revoked_at=now,
                )
                await append_audit_event(
                    conn,
                    action="user.deactivate",
                    actor_type="user",
                    actor_user_id=actor_user_id,
                    entity_type="user",
                    entity_id=user_id,
                    before_json=before,
                    after_json=after,
                    request_id=request_id,
                )

    return _profile_from_row(row)


def _reject_if_idle_or_expired(
    *,
    now: datetime,
    expires_at: datetime,
    last_seen_at: datetime,
    idle_timeout_seconds: int,
) -> None:
    if expires_at <= now:
        raise AppError(
            code="AUTH_SESSION_EXPIRED",
            message="Session has expired.",
            status_code=401,
        )
    idle_deadline = last_seen_at + timedelta(seconds=idle_timeout_seconds)
    if idle_deadline <= now:
        raise AppError(
            code="AUTH_SESSION_IDLE",
            message="Session idle timeout exceeded.",
            status_code=401,
        )


async def resolve_principal(
    pool: AsyncConnectionPool,
    *,
    raw_session_secret: str | None,
    method: str,
    csrf_header: str | None,
    settings: Settings | None = None,
    touch_last_seen: bool = True,
) -> AuthenticatedPrincipal:
    """Validate cookie session, idle/absolute expiry, active user, and CSRF on mutations."""
    cfg = settings or get_settings()
    if not raw_session_secret:
        raise AppError(
            code="AUTH_UNAUTHENTICATED",
            message="Authentication required.",
            status_code=401,
        )

    token_hash = hash_session_secret(raw_session_secret)
    now = _utcnow()

    async with pool.connection() as conn:
        async with conn.transaction():
            row = await repository.fetch_session_by_token_hash(conn, token_hash)
            if row is None:
                raise AppError(
                    code="AUTH_UNAUTHENTICATED",
                    message="Authentication required.",
                    status_code=401,
                )
            if row["revoked_at"] is not None:
                raise AppError(
                    code="AUTH_SESSION_REVOKED",
                    message="Session has been revoked.",
                    status_code=401,
                )
            if not row["is_active"]:
                raise AppError(
                    code="AUTH_USER_INACTIVE",
                    message="User account is inactive.",
                    status_code=401,
                )

            _reject_if_idle_or_expired(
                now=now,
                expires_at=row["expires_at"],
                last_seen_at=row["last_seen_at"],
                idle_timeout_seconds=cfg.SESSION_IDLE_TIMEOUT_SECONDS,
            )

            csrf_token = csrf_token_for_session(raw_session_secret)
            if method.upper() in _MUTATING_METHODS:
                if csrf_header is None or csrf_header == "":
                    raise AppError(
                        code="AUTH_CSRF_MISSING",
                        message="CSRF token required for mutating requests.",
                        status_code=403,
                    )
                if not csrf_tokens_match(csrf_token, csrf_header):
                    raise AppError(
                        code="AUTH_CSRF_INVALID",
                        message="CSRF token mismatch.",
                        status_code=403,
                    )

            if touch_last_seen:
                await repository.touch_session_last_seen(
                    conn,
                    session_id=row["session_id"],
                    last_seen_at=now,
                )
                last_seen = now
            else:
                last_seen = row["last_seen_at"]

    return AuthenticatedPrincipal(
        user=UserProfile(
            id=row["user_id"],
            email=row["email"],
            full_name=row["full_name"],
            role=row["role"],
            is_active=row["is_active"],
        ),
        session_id=row["session_id"],
        csrf_token=csrf_token,
        raw_session_secret=raw_session_secret,
        session_expires_at=row["expires_at"],
        session_last_seen_at=last_seen,
    )
