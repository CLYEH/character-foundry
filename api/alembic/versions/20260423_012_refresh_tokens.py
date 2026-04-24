"""refresh_tokens

Stores hashed refresh tokens for JWT auth (T-006). The raw token is a UUID
handed to the client; the DB keeps only its sha256 hex so a DB leak doesn't
compromise active sessions. `revoked_at` enables logout; `expires_at` enables
30-day refresh TTL per DECISIONS §6 B4.

Revision ID: 20260423_012
Revises: 20260423_011
Create Date: 2026-04-24

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260423_012"
down_revision: str | Sequence[str] | None = "20260423_011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "refresh_tokens",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # sha256 hex of the UUID token handed to the client (64 chars).
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )

    # Index on (user_id) for "logout all sessions" / audit lookups; the
    # token_hash UNIQUE already provides the lookup path for refresh/logout.
    op.create_index("idx_refresh_tokens_user", "refresh_tokens", ["user_id"])

    # Active-session lookup: scheduled cleanup jobs want cheap access to
    # unrevoked tokens past their expiry.
    op.create_index(
        "idx_refresh_tokens_active",
        "refresh_tokens",
        ["expires_at"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_refresh_tokens_active", table_name="refresh_tokens")
    op.drop_index("idx_refresh_tokens_user", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
