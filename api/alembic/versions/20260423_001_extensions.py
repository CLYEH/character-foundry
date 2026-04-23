"""extensions

Installs the Postgres extensions and the shared updated_at trigger function
that later tables reuse.

Revision ID: 20260423_001
Revises:
Create Date: 2026-04-23

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260423_001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


EXTENSIONS = ("uuid-ossp", "pgcrypto", "vector", "pg_trgm")


def upgrade() -> None:
    # plpgsql is installed by default in DBs cloned from template0, but not
    # template1. Install explicitly so the trigger function below always works.
    op.execute("CREATE EXTENSION IF NOT EXISTS plpgsql")
    for ext in EXTENSIONS:
        op.execute(f'CREATE EXTENSION IF NOT EXISTS "{ext}"')

    # Generic trigger function reused by every table with updated_at.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION update_updated_at_column()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS update_updated_at_column()")
    # Extensions are intentionally not dropped: they may be shared with other
    # databases / schemas and are cheap to keep installed.
