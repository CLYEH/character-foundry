from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base as _DeclarativeBase


class BaseAsset(_DeclarativeBase):
    """The confirmed, immutable look for a Character (1:1).

    Class name is `BaseAsset` rather than `Base` to avoid shadowing the
    SQLAlchemy declarative base imported from app.db.base.
    """

    __tablename__ = "bases"
    __table_args__ = (
        Index("idx_bases_character", "character_id"),
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
        unique=True,
    )
    from_checkpoint_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("checkpoints.id", ondelete="RESTRICT"),
        nullable=False,
    )
    image_key: Mapped[str] = mapped_column(Text, nullable=False)
    image_embedding: Mapped[list[float] | None] = mapped_column(
        Vector(768), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
