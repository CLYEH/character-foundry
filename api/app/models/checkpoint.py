from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Checkpoint(Base):
    """Immutable candidate image from a creation session."""

    __tablename__ = "checkpoints"
    __table_args__ = (
        UniqueConstraint("creation_session_id", "sequence", name="uq_session_sequence"),
        Index("idx_checkpoints_session", "creation_session_id", "sequence"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    creation_session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("creation_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    user_menu_selections: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    user_freeform_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reference_image_keys: Mapped[list[str] | None] = mapped_column(
        ARRAY(Text), nullable=True
    )
    seed: Mapped[str | None] = mapped_column(String(100), nullable=True)
    output_image_key: Mapped[str] = mapped_column(Text, nullable=False)
    # CLIP ViT-L/14 embedding. Modeled here so alembic autogenerate doesn't
    # see a DB-only column and emit a destructive DROP-COLUMN diff.
    output_image_embedding: Mapped[list[float] | None] = mapped_column(
        Vector(768), nullable=True
    )
    # Soft reference: generation_logs is partitioned and cannot be FK'd.
    generation_log_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    selected_as_base: Mapped[bool | None] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
