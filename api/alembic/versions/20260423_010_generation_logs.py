"""generation_logs (partitioned monthly)

Creates the RANGE-partitioned parent `generation_logs`, plus:
  - Three month partitions derived from execution time (current + next two).
    Computing the range at upgrade time means any future environment running
    `alembic upgrade head` for the first time gets partitions that cover its
    own clock, rather than the fixed 2026-04..06 range a hard-coded list
    would bake in.
  - A DEFAULT PARTITION as a safety net for months the scheduled rotation
    job (planning/data/lifecycle.md §4.2) hasn't created yet. Rows landing in
    default can be migrated into a named partition later.

Revision ID: 20260423_010
Revises: 20260423_009
Create Date: 2026-04-23

"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone

from alembic import op

revision: str = "20260423_010"
down_revision: str | Sequence[str] | None = "20260423_009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _month_ranges(anchor: datetime, count: int = 3) -> list[tuple[str, str, str]]:
    """Return (suffix, start_iso, end_iso) for `count` consecutive months
    beginning with the month containing `anchor`.

    Pure function so it's testable from the migration-tests side without
    going through Alembic.
    """
    year, month = anchor.year, anchor.month
    out: list[tuple[str, str, str]] = []
    for _ in range(count):
        next_year = year + 1 if month == 12 else year
        next_month = 1 if month == 12 else month + 1
        suffix = f"{year:04d}_{month:02d}"
        start = f"{year:04d}-{month:02d}-01"
        end = f"{next_year:04d}-{next_month:02d}-01"
        out.append((suffix, start, end))
        year, month = next_year, next_month
    return out


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

    # DEFAULT partition catches anything outside the named ranges. Must be
    # created BEFORE we start attaching named partitions — Postgres scans the
    # default when a new partition is attached, and scanning an empty default
    # is essentially free; scanning a populated one later is expensive.
    op.execute(
        """
        CREATE TABLE generation_logs_default
            PARTITION OF generation_logs
            DEFAULT
        """
    )
    op.execute(
        """
        CREATE INDEX idx_gen_logs_default_user_time
            ON generation_logs_default (user_id, started_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX idx_gen_logs_default_character
            ON generation_logs_default (character_id)
            WHERE character_id IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE INDEX idx_gen_logs_default_status
            ON generation_logs_default (status)
            WHERE status IN ('running', 'failed')
        """
    )

    # Named monthly partitions computed from execution time so a fresh
    # `alembic upgrade head` in any month always gets the right 3 partitions.
    for suffix, start, end in _month_ranges(datetime.now(timezone.utc), count=3):
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
    # Dropping the partitioned parent cascades to every named partition AND
    # the default partition.
    op.execute("DROP TABLE IF EXISTS generation_logs CASCADE")
