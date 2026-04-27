from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ReferenceImage(Base):
    """User-uploaded reference image attached to a creation session.

    Lifecycle is bound to the session: cascade delete cleans up the row
    when the session is removed, and the storage file is reaped by the
    same lifecycle sweep that handles the session's checkpoints
    (planning/data/lifecycle.md §3.2). Phase 1 has no separate cleanup
    for unused references — they sit until the session itself is gone.
    """

    __tablename__ = "reference_images"
    __table_args__ = (Index("idx_reference_images_session", "creation_session_id"),)

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
    uploaded_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(50), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
