"""Backend RBAC dependencies (Architecture §12; PRD three roles only)."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends

from modules.identity.deps import get_current_active_user
from modules.identity.schemas import UserProfile, UserRole
from shared.errors import AppError


def require_role(
    *allowed_roles: UserRole,
) -> Callable[..., Coroutine[Any, Any, UserProfile]]:
    """
    FastAPI dependency factory: caller must be an active user in allowed_roles.

    Unauthenticated callers fail in get_current_active_user (401).
    Wrong role raises AUTH_FORBIDDEN (403).
    """
    if not allowed_roles:
        raise ValueError("require_role requires at least one allowed role")
    allowed = frozenset(allowed_roles)

    async def _checker(
        user: Annotated[UserProfile, Depends(get_current_active_user)],
    ) -> UserProfile:
        if user.role not in allowed:
            raise AppError(
                code="AUTH_FORBIDDEN",
                message="Insufficient permissions for this operation.",
                status_code=403,
                details={"required_roles": sorted(allowed), "role": user.role},
            )
        return user

    return _checker


async def require_admin(
    user: Annotated[UserProfile, Depends(require_role("admin"))],
) -> UserProfile:
    """Convenience dependency: Admin role only."""
    return user
