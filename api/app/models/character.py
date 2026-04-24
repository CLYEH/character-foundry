from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Character(Base):
    """Skeleton row. base_id and creation_session_id get real FKs in T-003."""

    __tablename__ = "characters"
    __table_args__ = (
        CheckConstraint(
            "char_length(name) BETWEEN 1 AND 50",
            name="chk_characters_name_length",
        ),
        # PostgreSQL ARE does not support \p{...} unicode property escapes, so
        # we use a literal CJK Unified Ideographs range (U+4E00–U+9FFF) plus
        # ASCII alphanumerics, underscore and hyphen.
        CheckConstraint(
            "name ~ '^[一-鿿a-zA-Z0-9_-]+$'",
            name="chk_characters_name_chars",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="RESTRICT"),
        nullable=False,
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    slug: Mapped[str] = mapped_column(String(60), nullable=False)
    # FKs are added by T-003 migrations (007_bases, 005_creation_sessions) to
    # work around the circular reference with characters.
    base_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("bases.id", ondelete="SET NULL", use_alter=True),
        nullable=True,
    )
    creation_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("creation_sessions.id", ondelete="SET NULL", use_alter=True),
        nullable=True,
    )
    copied_from_character_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("characters.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
