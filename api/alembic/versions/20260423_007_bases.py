"""bases

Creates the per-character Base row and closes the characters.base_id FK that
004 intentionally left dangling.

Revision ID: 20260423_007
Revises: 20260423_006
Create Date: 2026-04-23

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260423_007"
down_revision: str | Sequence[str] | None = "20260423_006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE bases (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            character_id UUID NOT NULL UNIQUE
                REFERENCES characters(id) ON DELETE CASCADE,
            from_checkpoint_id UUID NOT NULL
                REFERENCES checkpoints(id) ON DELETE RESTRICT,
            image_key TEXT NOT NULL,
            image_embedding vector(768),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )

    # Close the circular reference from characters → bases that 004 left open.
    op.execute(
        """
        ALTER TABLE characters
            ADD CONSTRAINT fk_characters_base
            FOREIGN KEY (base_id) REFERENCES bases(id) ON DELETE SET NULL
        """
    )

    op.create_index("idx_bases_character", "bases", ["character_id"])
    op.execute(
        """
        CREATE INDEX idx_bases_embedding
            ON bases
            USING ivfflat (image_embedding vector_cosine_ops)
            WITH (lists = 100)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_bases_embedding")
    op.drop_index("idx_bases_character", table_name="bases")
    op.execute("ALTER TABLE characters DROP CONSTRAINT IF EXISTS fk_characters_base")
    op.drop_table("bases")
