"""reference_images

Per-session uploads used as conditioning input for checkpoint generation
(T-017). Each row is a thin handle over a storage key — the file itself
lives under the `checkpoints/{session_id}/references/{id}.{ext}` prefix
in the StorageBackend (planning/data/storage-layout.md §2). Cascade with
the session: when a session is deleted the rows go too, and the storage
files are reaped by the same lifecycle pass that cleans the session's
checkpoints.

Note: `checkpoints.reference_image_keys TEXT[]` (migration 006) holds the
keys actually used by a given checkpoint at generation time — this table
is the upload registry. The two together let the worker resolve a
caller-provided `reference_image_id` to bytes without exposing storage
keys to the client.

Revision ID: 20260425_013
Revises: 20260423_012
Create Date: 2026-04-25

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260425_013"
down_revision: str | Sequence[str] | None = "20260423_012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reference_images",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "creation_session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("creation_sessions.id", ondelete="CASCADE"),
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

    # Reference upload listings + checkpoint payload validation both look
    # up by session — single composite covers both access patterns.
    op.create_index(
        "idx_reference_images_session",
        "reference_images",
        ["creation_session_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_reference_images_session", table_name="reference_images")
    op.drop_table("reference_images")
