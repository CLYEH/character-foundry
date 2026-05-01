"""Pure DB ops for the partitioned `generation_logs` table.

The table is RANGE-partitioned by `started_at` (planning/data/db-schema.md
§3.9), so the PK is composite (id, started_at). Other tables hold
`generation_log_id` as a UUID-only soft FK because Postgres doesn't allow
real FKs into a partitioned parent.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.generation_log import GenerationLog


async def get_by_id(db: AsyncSession, log_id: uuid.UUID) -> GenerationLog | None:
    """Fetch a generation log by id alone.

    The table is RANGE-partitioned on `started_at`, so a query without
    the partition key cannot be partition-pruned — this is fine for the
    single-row lookups the motion-detail surface (T-034) needs (one row
    per request, looked up via the soft FK on the motion). Bulk reads
    should still scope by `started_at` to enable pruning.
    """
    stmt = select(GenerationLog).where(GenerationLog.id == log_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def insert_success(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    character_id: uuid.UUID | None,
    entity_type: str,
    entity_id: uuid.UUID,
    model_name: str,
    model_version: str,
    final_prompt: str,
    input_image_keys: list[str] | None,
    parameters: dict[str, Any] | None,
    cost_units: float,
    duration_ms: int,
    started_at: datetime,
    completed_at: datetime,
) -> GenerationLog:
    """Persist a `success` audit row and return it. The id is generated
    server-side; callers read `row.id` to populate the soft FK on the
    matching checkpoint / alias / motion."""
    row = GenerationLog(
        user_id=user_id,
        character_id=character_id,
        entity_type=entity_type,
        entity_id=entity_id,
        model_name=model_name,
        model_version=model_version,
        final_prompt=final_prompt,
        input_image_keys=input_image_keys,
        parameters=parameters,
        cost_units=Decimal(str(cost_units)),
        status="success",
        duration_ms=duration_ms,
        started_at=started_at,
        completed_at=completed_at,
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row
