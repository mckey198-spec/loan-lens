"""Enable required PostgreSQL extensions.

Revision ID: 0001_extensions
Revises:
Create Date: 2026-11-04

Enables pgcrypto, vector, and btree_gist only.
Does not enable citext (pending AGENT_RULES.md §2 decision).
Does not enable uuid-ossp (pgcrypto provides gen_random_uuid()).
Does not create product tables or policy_chunks.embedding.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_extensions"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist;")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS btree_gist;")
    op.execute("DROP EXTENSION IF EXISTS vector;")
    op.execute("DROP EXTENSION IF EXISTS pgcrypto;")
