"""Seed local dummy users for UI / API smoke testing.

Local-only. Do not run against production. Passwords are intentional
non-secrets matching the pytest fixture convention.

Usage (from repo root, venv active):

    python scripts/seed_users.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow `python scripts/seed_users.py` without installing the package.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import psycopg

from modules.identity.passwords import hash_password
from shared.settings import get_settings

# Fixed local credentials for manual sign-in at http://localhost:5173/login
SEED_USERS: tuple[dict[str, str], ...] = (
    {
        "email": "auth.officer@example.com",
        "full_name": "Local Test Officer",
        "role": "loan_officer",
        "password": "test-password-not-a-secret",
    },
    {
        "email": "auth.reviewer@example.com",
        "full_name": "Local Test Reviewer",
        "role": "reviewer",
        "password": "reviewer-password-not-a-secret",
    },
    {
        "email": "auth.admin@example.com",
        "full_name": "Local Test Admin",
        "role": "admin",
        "password": "admin-password-not-a-secret",
    },
)


def main() -> int:
    settings = get_settings()
    if settings.APP_ENV == "production":
        print("Refusing to seed users when APP_ENV=production.", file=sys.stderr)
        return 1

    now = datetime.now(timezone.utc)
    with psycopg.connect(settings.DATABASE_URL) as conn:
        with conn.cursor() as cur:
            for user in SEED_USERS:
                password_hash = hash_password(user["password"], settings=settings)
                cur.execute(
                    """
                    INSERT INTO users (
                        email, full_name, role, password_hash,
                        is_active, created_at, updated_at
                    )
                    VALUES (%s, %s, %s::user_role, %s, true, %s, %s)
                    ON CONFLICT (email) DO UPDATE SET
                        full_name = EXCLUDED.full_name,
                        role = EXCLUDED.role,
                        password_hash = EXCLUDED.password_hash,
                        is_active = true,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        user["email"],
                        user["full_name"],
                        user["role"],
                        password_hash,
                        now,
                        now,
                    ),
                )
        conn.commit()

    print("Seeded local users (email / password):")
    for user in SEED_USERS:
        print(f"  {user['role']:13}  {user['email']}  /  {user['password']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
