"""characters skeleton

Creates the characters table without FKs to bases / creation_sessions — those
tables do not exist yet and their FKs are added in T-003 to avoid a circular
dependency during migration ordering. Also attaches the updated_at trigger.

Revision ID: 20260423_004
Revises: 20260423_003
Create Date: 2026-04-23

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260423_004"
down_revision: str | Sequence[str] | None = "20260423_003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "characters",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "owner_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("slug", sa.String(length=60), nullable=False),
        # FK added in T-003 once bases exists.
        sa.Column("base_id", postgresql.UUID(as_uuid=True), nullable=True),
        # FK added in T-003 once creation_sessions exists.
        sa.Column(
            "creation_session_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column(
            "copied_from_character_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("characters.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "char_length(name) BETWEEN 1 AND 50",
            name="chk_characters_name_length",
        ),
        sa.CheckConstraint(
            r"name ~ '^[\p{Han}a-zA-Z0-9_\-]+$'",
            name="chk_characters_name_chars",
        ),
    )

    # Soft-delete aware uniqueness — different owners may reuse a name; a
    # soft-deleted row doesn't block a re-create.
    op.create_index(
        "uq_characters_owner_name",
        "characters",
        ["owner_id", "name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_characters_owner_slug",
        "characters",
        ["owner_id", "slug"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "idx_characters_team",
        "characters",
        ["team_id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "idx_characters_owner",
        "characters",
        ["owner_id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # Fuzzy name search — pg_trgm GIN index, filtered by not-deleted.
    op.execute(
        """
        CREATE INDEX idx_characters_name_trgm
            ON characters
            USING gin (name gin_trgm_ops)
            WHERE deleted_at IS NULL
        """
    )

    op.execute(
        """
        CREATE TRIGGER trg_characters_updated_at
            BEFORE UPDATE ON characters
            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_characters_updated_at ON characters")
    op.execute("DROP INDEX IF EXISTS idx_characters_name_trgm")
    op.drop_index("idx_characters_owner", table_name="characters")
    op.drop_index("idx_characters_team", table_name="characters")
    op.drop_index("uq_characters_owner_slug", table_name="characters")
    op.drop_index("uq_characters_owner_name", table_name="characters")
    op.drop_table("characters")
