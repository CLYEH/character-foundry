"""aliases

Creates the Alias table (soft-deletable variants hanging off a Character's
Base).

Revision ID: 20260423_008
Revises: 20260423_007
Create Date: 2026-04-23

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260423_008"
down_revision: str | Sequence[str] | None = "20260423_007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE aliases (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            character_id UUID NOT NULL
                REFERENCES characters(id) ON DELETE CASCADE,
            name VARCHAR(50) NOT NULL,
            prompt TEXT NOT NULL,
            user_freeform_note TEXT,
            input_mode VARCHAR(30) NOT NULL,
            mask_data JSONB,
            image_key TEXT NOT NULL,
            image_embedding vector(768),
            -- Soft reference to partitioned generation_logs.
            generation_log_id UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            deleted_at TIMESTAMPTZ,
            CONSTRAINT chk_aliases_input_mode
                CHECK (input_mode IN ('image2image', 'inpaint',
                                      'text2image', 'mixed')),
            CONSTRAINT chk_aliases_name_length
                CHECK (char_length(name) BETWEEN 1 AND 50),
            -- Literal CJK range U+4E00–U+9FFF; PostgreSQL ARE has no \\p{Han}.
            CONSTRAINT chk_aliases_name_chars
                CHECK (name ~ '^[一-鿿a-zA-Z0-9_-]+$')
        )
        """
    )

    # Soft-delete aware unique: per-character name must be unique among
    # non-deleted aliases; soft-deleted rows do not block a re-create.
    op.create_index(
        "uq_aliases_character_name",
        "aliases",
        ["character_id", "name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "idx_aliases_character",
        "aliases",
        ["character_id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.execute(
        """
        CREATE INDEX idx_aliases_embedding
            ON aliases
            USING ivfflat (image_embedding vector_cosine_ops)
            WITH (lists = 100)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_aliases_embedding")
    op.drop_index("idx_aliases_character", table_name="aliases")
    op.drop_index("uq_aliases_character_name", table_name="aliases")
    op.drop_table("aliases")
