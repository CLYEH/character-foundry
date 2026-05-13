from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RefreshTokenSource(enum.StrEnum):
    """Which auth path minted this refresh token.

    `JWT` = legacy email+password login (T-006). `OAUTH` = Authentik
    Authorization Code + PKCE delegated flow (T-3.5b). The split exists so
    the refresh endpoint can route correctly without re-deriving source from
    token shape. T-055 adds the column; the read path that branches on it
    lands later.
    """

    JWT = "jwt"
    OAUTH = "oauth"


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    __table_args__ = (
        Index("idx_refresh_tokens_user", "user_id"),
        Index(
            "idx_refresh_tokens_active",
            "expires_at",
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    token_source: Mapped[RefreshTokenSource] = mapped_column(
        Enum(
            RefreshTokenSource,
            name="refresh_token_source",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
            create_type=False,
        ),
        nullable=False,
        default=RefreshTokenSource.JWT,
    )
