"""creation_sessions

Adds the creation_sessions table and closes the circular reference by adding
characters.creation_session_id → creation_sessions(id).

Revision ID: 20260423_005
Revises: 20260423_004
Create Date: 2026-04-23

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260423_005"
down_revision: str | Sequence[str] | None = "20260423_004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "creation_sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # Nullable while status = 'in_progress' — Character is created when the
        # session completes (see planning/data/lifecycle.md §2.1).
        sa.Column(
            "character_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("characters.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "initiator_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("input_mode", sa.String(length=20), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'in_progress'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "input_mode IN ('template', 'reference')",
            name="chk_creation_sessions_input_mode",
        ),
        sa.CheckConstraint(
            "status IN ('in_progress', 'completed', 'abandoned')",
            name="chk_creation_sessions_status",
        ),
    )

    # Close the circular reference from characters → creation_sessions that
    # 004 intentionally left dangling.
    op.create_foreign_key(
        "fk_characters_creation_session",
        source_table="characters",
        referent_table="creation_sessions",
        local_cols=["creation_session_id"],
        remote_cols=["id"],
        ondelete="SET NULL",
    )

    op.create_index(
        "idx_sessions_initiator",
        "creation_sessions",
        ["initiator_id"],
    )
    op.create_index(
        "idx_sessions_character",
        "creation_sessions",
        ["character_id"],
        postgresql_where=sa.text("character_id IS NOT NULL"),
    )
    op.create_index(
        "idx_sessions_in_progress",
        "creation_sessions",
        ["status"],
        postgresql_where=sa.text("status = 'in_progress'"),
    )


def downgrade() -> None:
    op.drop_index("idx_sessions_in_progress", table_name="creation_sessions")
    op.drop_index("idx_sessions_character", table_name="creation_sessions")
    op.drop_index("idx_sessions_initiator", table_name="creation_sessions")
    op.drop_constraint(
        "fk_characters_creation_session",
        "characters",
        type_="foreignkey",
    )
    op.drop_table("creation_sessions")
