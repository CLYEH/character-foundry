from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Alias(Base):
    __tablename__ = "aliases"
    __table_args__ = (
        CheckConstraint(
            "input_mode IN ('image2image', 'inpaint', 'text2image', 'mixed')",
            name="chk_aliases_input_mode",
        ),
        CheckConstraint(
            "char_length(name) BETWEEN 1 AND 50",
            name="chk_aliases_name_length",
        ),
        CheckConstraint(
            "name ~ '^[一-鿿a-zA-Z0-9_-]+$'",
            name="chk_aliases_name_chars",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    character_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("characters.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    user_freeform_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_mode: Mapped[str] = mapped_column(String(30), nullable=False)
    mask_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    image_key: Mapped[str] = mapped_column(Text, nullable=False)
    image_embedding: Mapped[list[float] | None] = mapped_column(
        Vector(768), nullable=True
    )
    # Soft reference to partitioned generation_logs.
    generation_log_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
