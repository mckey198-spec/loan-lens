"""PostgreSQL async connection pool (psycopg3 + psycopg_pool)."""

from __future__ import annotations

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool


def create_pool(database_url: str) -> AsyncConnectionPool:
    """Build a closed pool; caller opens it during FastAPI lifespan."""
    return AsyncConnectionPool(
        conninfo=database_url,
        kwargs={"row_factory": dict_row},
        open=False,
        min_size=1,
        max_size=10,
    )
