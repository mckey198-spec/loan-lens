"""Raw SQL access for users and user_sessions (no ORM)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection


_USER_COLUMNS = """
    id, email, full_name, role::text AS role, password_hash, is_active,
    created_at, updated_at
"""


async def fetch_user_by_email(
    conn: AsyncConnection[Any],
    email: str,
) -> dict[str, Any] | None:
    row = await conn.execute(
        f"""
        SELECT {_USER_COLUMNS}
        FROM users
        WHERE email = %s
        """,
        (email,),
    )
    return await row.fetchone()


async def fetch_user_by_id(
    conn: AsyncConnection[Any],
    user_id: UUID,
) -> dict[str, Any] | None:
    row = await conn.execute(
        f"""
        SELECT {_USER_COLUMNS}
        FROM users
        WHERE id = %s
        """,
        (user_id,),
    )
    return await row.fetchone()


async def insert_user(
    conn: AsyncConnection[Any],
    *,
    email: str,
    full_name: str,
    role: str,
    password_hash: str,
    is_active: bool,
    created_at: datetime,
    updated_at: datetime,
) -> dict[str, Any]:
    row = await conn.execute(
        f"""
        INSERT INTO users (
            email, full_name, role, password_hash, is_active, created_at, updated_at
        )
        VALUES (%s, %s, %s::user_role, %s, %s, %s, %s)
        RETURNING {_USER_COLUMNS}
        """,
        (email, full_name, role, password_hash, is_active, created_at, updated_at),
    )
    result = await row.fetchone()
    assert result is not None
    return result


async def list_users(
    conn: AsyncConnection[Any],
    *,
    offset: int,
    limit: int,
) -> tuple[list[dict[str, Any]], int]:
    count_row = await conn.execute("SELECT COUNT(*)::int AS total FROM users")
    count_result = await count_row.fetchone()
    assert count_result is not None
    total = int(count_result["total"])

    rows = await conn.execute(
        f"""
        SELECT {_USER_COLUMNS}
        FROM users
        ORDER BY created_at ASC, id ASC
        OFFSET %s
        LIMIT %s
        """,
        (offset, limit),
    )
    items = await rows.fetchall()
    return list(items), total


async def update_user(
    conn: AsyncConnection[Any],
    *,
    user_id: UUID,
    role: str | None,
    is_active: bool | None,
    updated_at: datetime,
) -> dict[str, Any] | None:
    """Update role and/or is_active. Returns None when the user id does not exist."""
    if role is None and is_active is None:
        return await fetch_user_by_id(conn, user_id)

    sets: list[str] = ["updated_at = %s"]
    params: list[Any] = [updated_at]
    if role is not None:
        sets.append("role = %s::user_role")
        params.append(role)
    if is_active is not None:
        sets.append("is_active = %s")
        params.append(is_active)
    params.append(user_id)

    row = await conn.execute(
        f"""
        UPDATE users
        SET {", ".join(sets)}
        WHERE id = %s
        RETURNING {_USER_COLUMNS}
        """,
        tuple(params),
    )
    return await row.fetchone()


async def insert_session(
    conn: AsyncConnection[Any],
    *,
    user_id: UUID,
    token_hash: str,
    created_at: datetime,
    expires_at: datetime,
    last_seen_at: datetime,
) -> UUID:
    row = await conn.execute(
        """
        INSERT INTO user_sessions (
            user_id, token_hash, created_at, expires_at, last_seen_at, revoked_at
        )
        VALUES (%s, %s, %s, %s, %s, NULL)
        RETURNING id
        """,
        (user_id, token_hash, created_at, expires_at, last_seen_at),
    )
    result = await row.fetchone()
    assert result is not None
    return result["id"]


async def fetch_session_by_token_hash(
    conn: AsyncConnection[Any],
    token_hash: str,
) -> dict[str, Any] | None:
    row = await conn.execute(
        """
        SELECT
            s.id AS session_id,
            s.user_id,
            s.token_hash,
            s.created_at,
            s.expires_at,
            s.last_seen_at,
            s.revoked_at,
            u.email,
            u.full_name,
            u.role::text AS role,
            u.is_active
        FROM user_sessions s
        INNER JOIN users u ON u.id = s.user_id
        WHERE s.token_hash = %s
        """,
        (token_hash,),
    )
    return await row.fetchone()


async def touch_session_last_seen(
    conn: AsyncConnection[Any],
    *,
    session_id: UUID,
    last_seen_at: datetime,
) -> None:
    await conn.execute(
        """
        UPDATE user_sessions
        SET last_seen_at = %s
        WHERE id = %s AND revoked_at IS NULL
        """,
        (last_seen_at, session_id),
    )


async def revoke_session_by_id(
    conn: AsyncConnection[Any],
    *,
    session_id: UUID,
    revoked_at: datetime,
) -> bool:
    row = await conn.execute(
        """
        UPDATE user_sessions
        SET revoked_at = %s
        WHERE id = %s AND revoked_at IS NULL
        RETURNING id
        """,
        (revoked_at, session_id),
    )
    return await row.fetchone() is not None


async def revoke_session_by_token_hash(
    conn: AsyncConnection[Any],
    *,
    token_hash: str,
    revoked_at: datetime,
) -> bool:
    row = await conn.execute(
        """
        UPDATE user_sessions
        SET revoked_at = %s
        WHERE token_hash = %s AND revoked_at IS NULL
        RETURNING id
        """,
        (revoked_at, token_hash),
    )
    return await row.fetchone() is not None


async def revoke_all_sessions_for_user(
    conn: AsyncConnection[Any],
    *,
    user_id: UUID,
    revoked_at: datetime,
) -> int:
    """Set revoked_at on every open session for the user (deactivation path)."""
    row = await conn.execute(
        """
        UPDATE user_sessions
        SET revoked_at = %s
        WHERE user_id = %s AND revoked_at IS NULL
        RETURNING id
        """,
        (revoked_at, user_id),
    )
    rows = await row.fetchall()
    return len(rows)
