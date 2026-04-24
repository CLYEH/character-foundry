from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Motion(Base):
    """Motion video. Polymorphic parent: exactly one of base_id or alias_id."""

    __tablename__ = "motions"
    __table_args__ = (
        CheckConstraint(
            "motion_type IN ('preset_wave', 'preset_nod', 'preset_gesture', "
            "'preset_happy', 'preset_idle', 'custom')",
            name="chk_motions_type",
        ),
        CheckConstraint(
            "(base_id IS NOT NULL AND alias_id IS NULL) OR "
            "(base_id IS NULL AND alias_id IS NOT NULL)",
            name="chk_motions_exactly_one_parent",
        ),
        CheckConstraint(
            "char_length(name) BETWEEN 1 AND 50",
            name="chk_motions_name_length",
        ),
        CheckConstraint(
            "name ~ '^[一-鿿a-zA-Z0-9_-]+$'",
            name="chk_motions_name_chars",
        ),
        CheckConstraint(
            "motion_type != 'custom' OR description IS NOT NULL",
            name="chk_motions_custom_has_description",
        ),
        # Same-parent name uniqueness (split per parent kind). Partial UNIQUE
        # that would silently allow duplicate motion names if autogenerate
        # dropped it.
        Index(
            "uq_motions_base_name",
            "base_id",
            "name",
            unique=True,
            postgresql_where=text("base_id IS NOT NULL AND deleted_at IS NULL"),
        ),
        Index(
            "uq_motions_alias_name",
            "alias_id",
            "name",
            unique=True,
            postgresql_where=text("alias_id IS NOT NULL AND deleted_at IS NULL"),
        ),
        Index(
            "idx_motions_base",
            "base_id",
            postgresql_where=text("base_id IS NOT NULL AND deleted_at IS NULL"),
        ),
        Index(
            "idx_motions_alias",
            "alias_id",
            postgresql_where=text("alias_id IS NOT NULL AND deleted_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    base_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("bases.id", ondelete="CASCADE"),
        nullable=True,
    )
    alias_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("aliases.id", ondelete="CASCADE"),
        nullable=True,
    )
    motion_type: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    video_key: Mapped[str] = mapped_column(Text, nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    generation_log_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
