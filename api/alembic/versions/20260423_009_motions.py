"""motions

Polymorphic motions (Option B): each row hangs off exactly one of base_id or
alias_id, enforced by CHECK.

Revision ID: 20260423_009
Revises: 20260423_008
Create Date: 2026-04-23

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260423_009"
down_revision: str | Sequence[str] | None = "20260423_008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE motions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            base_id UUID REFERENCES bases(id) ON DELETE CASCADE,
            alias_id UUID REFERENCES aliases(id) ON DELETE CASCADE,
            motion_type VARCHAR(30) NOT NULL,
            name VARCHAR(50) NOT NULL,
            description TEXT,
            video_key TEXT NOT NULL,
            duration_ms INT,
            -- Soft reference to partitioned generation_logs.
            generation_log_id UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            deleted_at TIMESTAMPTZ,

            CONSTRAINT chk_motions_type
                CHECK (motion_type IN (
                    'preset_wave', 'preset_nod', 'preset_gesture',
                    'preset_happy', 'preset_idle', 'custom'
                )),

            -- Exactly one parent — Option B from db-schema §3.8.
            CONSTRAINT chk_motions_exactly_one_parent
                CHECK (
                    (base_id IS NOT NULL AND alias_id IS NULL) OR
                    (base_id IS NULL AND alias_id IS NOT NULL)
                ),

            CONSTRAINT chk_motions_name_length
                CHECK (char_length(name) BETWEEN 1 AND 50),
            CONSTRAINT chk_motions_name_chars
                CHECK (name ~ '^[一-鿿a-zA-Z0-9_-]+$'),

            -- Custom motions must carry a description.
            CONSTRAINT chk_motions_custom_has_description
                CHECK (motion_type != 'custom' OR description IS NOT NULL)
        )
        """
    )

    # Same-parent name uniqueness — separate per base_id / alias_id.
    op.create_index(
        "uq_motions_base_name",
        "motions",
        ["base_id", "name"],
        unique=True,
        postgresql_where=sa.text("base_id IS NOT NULL AND deleted_at IS NULL"),
    )
    op.create_index(
        "uq_motions_alias_name",
        "motions",
        ["alias_id", "name"],
        unique=True,
        postgresql_where=sa.text("alias_id IS NOT NULL AND deleted_at IS NULL"),
    )
    op.create_index(
        "idx_motions_base",
        "motions",
        ["base_id"],
        postgresql_where=sa.text("base_id IS NOT NULL AND deleted_at IS NULL"),
    )
    op.create_index(
        "idx_motions_alias",
        "motions",
        ["alias_id"],
        postgresql_where=sa.text("alias_id IS NOT NULL AND deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_motions_alias", table_name="motions")
    op.drop_index("idx_motions_base", table_name="motions")
    op.drop_index("uq_motions_alias_name", table_name="motions")
    op.drop_index("uq_motions_base_name", table_name="motions")
    op.drop_table("motions")
