"""checkpoints

Creates the immutable per-session checkpoint rows, including the CLIP embedding
column and IVFFlat index used for similarity lookups.

Revision ID: 20260423_006
Revises: 20260423_005
Create Date: 2026-04-23

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260423_006"
down_revision: str | Sequence[str] | None = "20260423_005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE checkpoints (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            creation_session_id UUID NOT NULL
                REFERENCES creation_sessions(id) ON DELETE CASCADE,
            sequence INT NOT NULL,
            prompt TEXT NOT NULL,
            user_menu_selections JSONB,
            user_freeform_note TEXT,
            reference_image_keys TEXT[],
            seed VARCHAR(100),
            output_image_key TEXT NOT NULL,
            output_image_embedding vector(768),
            -- Soft reference: generation_logs is partitioned and cannot be the
            -- target of a real FK (see 010_generation_logs). Consistency is
            -- enforced by the application layer.
            generation_log_id UUID,
            selected_as_base BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_session_sequence
                UNIQUE (creation_session_id, sequence)
        )
        """
    )

    op.create_index(
        "idx_checkpoints_session",
        "checkpoints",
        ["creation_session_id", "sequence"],
    )

    # IVFFlat index for cosine similarity search. Phase 1 has little data so
    # the index starts effectively empty; `REINDEX` once rows accumulate.
    op.execute(
        """
        CREATE INDEX idx_checkpoints_embedding
            ON checkpoints
            USING ivfflat (output_image_embedding vector_cosine_ops)
            WITH (lists = 100)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_checkpoints_embedding")
    op.drop_index("idx_checkpoints_session", table_name="checkpoints")
    op.drop_table("checkpoints")
