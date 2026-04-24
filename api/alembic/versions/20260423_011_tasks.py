"""tasks

Source of truth for async task state (arq queue -> this table -> API).
See db-schema §3.11 and lifecycle §2.5.

Revision ID: 20260423_011
Revises: 20260423_010
Create Date: 2026-04-23

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260423_011"
down_revision: str | Sequence[str] | None = "20260423_010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE tasks (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,

            task_type VARCHAR(50) NOT NULL,

            status VARCHAR(20) NOT NULL DEFAULT 'queued',

            -- Polymorphic soft reference — no FK because entity_id can point
            -- to any of several tables. Application layer enforces consistency.
            entity_type VARCHAR(30),
            entity_id UUID,

            progress REAL,
            estimated_duration_ms INT,

            input_payload JSONB NOT NULL,
            result JSONB,
            error JSONB,

            queued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            started_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,

            cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
            cancel_requested_at TIMESTAMPTZ,

            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT chk_tasks_task_type
                CHECK (task_type IN (
                    'create_checkpoint', 'create_alias', 'create_motion',
                    'export_zip', 'copy_character'
                )),

            CONSTRAINT chk_tasks_status
                CHECK (status IN (
                    'queued', 'running', 'completed', 'failed', 'cancelled'
                )),

            CONSTRAINT chk_tasks_entity_type
                CHECK (entity_type IS NULL OR entity_type IN (
                    'checkpoint', 'alias', 'motion', 'character', 'export'
                )),

            CONSTRAINT chk_tasks_progress_range
                CHECK (progress IS NULL OR (progress >= 0 AND progress <= 1)),

            -- Terminal status must carry a completed_at; non-terminal must not.
            CONSTRAINT chk_tasks_terminal_completed_at
                CHECK (
                    (status IN ('queued', 'running') AND completed_at IS NULL)
                    OR
                    (status IN ('completed', 'failed', 'cancelled')
                        AND completed_at IS NOT NULL)
                ),

            -- result / error are mutually exclusive.
            CONSTRAINT chk_tasks_result_error_mutex
                CHECK (NOT (result IS NOT NULL AND error IS NOT NULL))
        )
        """
    )

    op.create_index(
        "idx_tasks_user_status_created",
        "tasks",
        ["user_id", "status", sa.text("created_at DESC")],
    )

    op.create_index(
        "idx_tasks_active",
        "tasks",
        ["queued_at"],
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )

    op.create_index(
        "idx_tasks_entity",
        "tasks",
        ["entity_type", "entity_id"],
        postgresql_where=sa.text("entity_id IS NOT NULL"),
    )

    op.create_index(
        "idx_tasks_cancel_pending",
        "tasks",
        ["id"],
        postgresql_where=sa.text(
            "cancel_requested = TRUE AND status = 'running'"
        ),
    )


def downgrade() -> None:
    op.drop_index("idx_tasks_cancel_pending", table_name="tasks")
    op.drop_index("idx_tasks_entity", table_name="tasks")
    op.drop_index("idx_tasks_active", table_name="tasks")
    op.drop_index("idx_tasks_user_status_created", table_name="tasks")
    op.drop_table("tasks")
