"""Alembic runtime environment.

target_metadata stays empty: revisions are handwritten SQL only.
Autogenerate is disabled. Application packages must not import sqlalchemy.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from shared.settings import get_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Empty metadata: no ORM models, no autogenerate (AGENT_RULES.md §5, TOOLS.md §3).
target_metadata = None


def _database_url() -> str:
    """Load DATABASE_URL from settings; use psycopg3 dialect for SQLAlchemy."""
    url = get_settings().DATABASE_URL
    if url.startswith("postgresql+"):
        return url
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url.removeprefix("postgres://")
    return url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (SQL script emission)."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (live database connection)."""
    connectable = create_engine(_database_url(), poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
