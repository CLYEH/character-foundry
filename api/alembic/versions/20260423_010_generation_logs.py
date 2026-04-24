"""generation_logs (partitioned monthly)

Creates the RANGE-partitioned parent generation_logs, plus the first three
partitions (current month + next two). Later months are created by a
scheduled job — see planning/data/lifecycle.md §4.2.

Revision ID: 20260423_010
Revises: 20260423_009
Create Date: 2026-04-23

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260423_010"
down_revision: str | Sequence[str] | None = "20260423_009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Bootstrap partitions. Scheduled job takes over for subsequent months.
BOOTSTRAP_PARTITIONS = (
    ("2026_04", "2026-04-01", "2026-05-01"),
    ("2026_05", "2026-05-01", "2026-06-01"),
    ("2026_06", "2026-06-01", "2026-07-01"),
)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE generation_logs (
            id UUID NOT NULL DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            character_id UUID REFERENCES characters(id) ON DELETE SET NULL,
            entity_type VARCHAR(20) NOT NULL,
            entity_id UUID,
            model_name VARCHAR(50) NOT NULL,
            model_version VARCHAR(30),
            final_prompt TEXT NOT NULL,
            input_image_keys TEXT[],
            parameters JSONB,
            cost_units DECIMAL(10, 4) NOT NULL DEFAULT 0,
            status VARCHAR(20) NOT NULL,
            error_message TEXT,
            duration_ms INT,
            started_at TIMESTAMPTZ NOT NULL,
            completed_at TIMESTAMPTZ,

            PRIMARY KEY (id, started_at),

            CONSTRAINT chk_gen_logs_entity_type
                CHECK (entity_type IN ('checkpoint', 'alias', 'motion')),
            CONSTRAINT chk_gen_logs_status
                CHECK (status IN ('success', 'failed', 'timeout', 'running'))
        ) PARTITION BY RANGE (started_at)
        """
    )

    for suffix, start, end in BOOTSTRAP_PARTITIONS:
        op.execute(
            f"""
            CREATE TABLE generation_logs_{suffix}
                PARTITION OF generation_logs
                FOR VALUES FROM ('{start}') TO ('{end}')
            """
        )
        op.execute(
            f"""
            CREATE INDEX idx_gen_logs_{suffix}_user_time
                ON generation_logs_{suffix} (user_id, started_at DESC)
            """
        )
        op.execute(
            f"""
            CREATE INDEX idx_gen_logs_{suffix}_character
                ON generation_logs_{suffix} (character_id)
                WHERE character_id IS NOT NULL
            """
        )
        op.execute(
            f"""
            CREATE INDEX idx_gen_logs_{suffix}_status
                ON generation_logs_{suffix} (status)
                WHERE status IN ('running', 'failed')
            """
        )


def downgrade() -> None:
    # Dropping the parent drops all partitions automatically.
    op.execute("DROP TABLE IF EXISTS generation_logs CASCADE")
