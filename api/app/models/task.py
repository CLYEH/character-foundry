from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    REAL,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    desc,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Task(Base):
    """Async task row — source of truth for arq task lifecycle."""

    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(
            "task_type IN ('create_checkpoint', 'create_alias', "
            "'create_motion', 'export_zip', 'copy_character')",
            name="chk_tasks_task_type",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
            name="chk_tasks_status",
        ),
        CheckConstraint(
            "entity_type IS NULL OR entity_type IN "
            "('checkpoint', 'alias', 'motion', 'character', 'export')",
            name="chk_tasks_entity_type",
        ),
        CheckConstraint(
            "progress IS NULL OR (progress >= 0 AND progress <= 1)",
            name="chk_tasks_progress_range",
        ),
        CheckConstraint(
            "(status IN ('queued', 'running') AND completed_at IS NULL) OR "
            "(status IN ('completed', 'failed', 'cancelled') "
            "AND completed_at IS NOT NULL)",
            name="chk_tasks_terminal_completed_at",
        ),
        CheckConstraint(
            "NOT (result IS NOT NULL AND error IS NOT NULL)",
            name="chk_tasks_result_error_mutex",
        ),
        Index(
            "idx_tasks_user_status_created",
            "user_id",
            "status",
            desc("created_at"),
        ),
        Index(
            "idx_tasks_active",
            "queued_at",
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
        Index(
            "idx_tasks_entity",
            "entity_type",
            "entity_id",
            postgresql_where=text("entity_id IS NOT NULL"),
        ),
        Index(
            "idx_tasks_cancel_pending",
            "id",
            postgresql_where=text(
                "cancel_requested = TRUE AND status = 'running'"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    task_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'queued'"),
    )

    entity_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    # REAL (float4) — matches migration 011 DDL. `Float` would map to
    # DOUBLE PRECISION and drift-check would flag it every run.
    progress: Mapped[float | None] = mapped_column(REAL, nullable=True)
    estimated_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    input_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    cancel_requested: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("FALSE"),
    )
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
