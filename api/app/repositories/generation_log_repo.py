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

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.generation_log import GenerationLog


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
