from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class GenerationLog(Base):
    """Audit row for each AI model call.

    The underlying table is RANGE-partitioned by started_at, so:
      - PK is composite (id, started_at) rather than id alone
      - Other tables may hold generation_log_id but cannot FK to it
        (Postgres doesn't allow FKs to reference a partitioned table).
    """

    __tablename__ = "generation_logs"
    __table_args__ = (
        CheckConstraint(
            "entity_type IN ('checkpoint', 'alias', 'motion')",
            name="chk_gen_logs_entity_type",
        ),
        CheckConstraint(
            "status IN ('success', 'failed', 'timeout', 'running')",
            name="chk_gen_logs_status",
        ),
        {"postgresql_partition_by": "RANGE (started_at)"},
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
    character_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("characters.id", ondelete="SET NULL"),
        nullable=True,
    )
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    model_name: Mapped[str] = mapped_column(String(50), nullable=False)
    model_version: Mapped[str | None] = mapped_column(String(30), nullable=True)
    final_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    input_image_keys: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    parameters: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    cost_units: Mapped[Decimal] = mapped_column(
        Numeric(10, 4),
        nullable=False,
        server_default=text("0"),
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Part of the composite PK (partition key).
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
