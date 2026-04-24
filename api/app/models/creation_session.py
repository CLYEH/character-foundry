from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CreationSession(Base):
    __tablename__ = "creation_sessions"
    __table_args__ = (
        CheckConstraint(
            "input_mode IN ('template', 'reference')",
            name="chk_creation_sessions_input_mode",
        ),
        CheckConstraint(
            "status IN ('in_progress', 'completed', 'abandoned')",
            name="chk_creation_sessions_status",
        ),
        Index("idx_sessions_initiator", "initiator_id"),
        Index(
            "idx_sessions_character",
            "character_id",
            postgresql_where=text("character_id IS NOT NULL"),
        ),
        Index(
            "idx_sessions_in_progress",
            "status",
            postgresql_where=text("status = 'in_progress'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    # Nullable while in_progress — Character is created on completion.
    character_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("characters.id", ondelete="CASCADE"),
        nullable=True,
    )
    initiator_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    input_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'in_progress'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
