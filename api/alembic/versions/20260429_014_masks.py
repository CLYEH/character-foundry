"""masks

Per-character inpaint mask uploads (T-035 schema; populated by the alias
upload endpoint added in T-031). Each row is a thin handle over a storage
key — the mask PNG itself lives under
`creation-sessions/{character_id}/masks/{mask_id}.png` in the
StorageBackend (planning/data/storage-layout.md, with mask path mirrored
from T-031 ticket Notes). Cascade with the character: when a character
is deleted the masks row goes with it, and the storage file is reaped
by the lifecycle pass that handles the character's other artefacts.

Owned by `characters` rather than `creation_sessions` because alias
generation — the only consumer — runs after Base lock-in, when the
session is no longer mutable.

Revision ID: 20260429_014
Revises: 20260425_013
Create Date: 2026-04-29

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260429_014"
down_revision: str | Sequence[str] | None = "20260425_013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "masks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "character_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("characters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "uploaded_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.String(length=50), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )

    # Mask listings + alias-create payload validation both look up by
    # character — single composite covers both access patterns.
    op.create_index(
        "idx_masks_character",
        "masks",
        ["character_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_masks_character", table_name="masks")
    op.drop_table("masks")
