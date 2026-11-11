"""Identity module: users, Argon2id passwords, server-side sessions, RBAC."""

from modules.identity.deps import get_current_active_user, get_current_principal
from modules.identity.passwords import hash_password, verify_password
from modules.identity.rbac import require_admin, require_role
from modules.identity.schemas import LoginRequest, LoginResponse, UserProfile
from modules.identity.service import login, logout, revoke_sessions_for_user

__all__ = [
    "LoginRequest",
    "LoginResponse",
    "UserProfile",
    "get_current_active_user",
    "get_current_principal",
    "hash_password",
    "verify_password",
    "login",
    "logout",
    "require_admin",
    "require_role",
    "revoke_sessions_for_user",
]
