"""Redis-backed atomic sequence allocator for checkpoint sequences.

`checkpoints.sequence` carries a UNIQUE constraint on `(creation_session_id,
sequence)` and the worker writes the row only AFTER successful image
generation (planning/backend/task-queue.md §3.5). That rules out
`COUNT(checkpoints) + 1` — multiple in-flight tasks would all reserve the
same sequence and the second worker would crash on UNIQUE violation.

Redis `INCR seq:checkpoint:{session_id}` is atomic and covers in-flight
work because it doesn't depend on any committed DB state. T-016 seeds the
key with `0` at session creation; the first INCR returns 1.

Crash recovery:
- If the Redis key disappears (Redis restart, eviction) the allocator
  rebuilds a baseline from `MAX(checkpoint.sequence)` AND
  `MAX(task.input_payload->>'sequence')` for in-flight create_checkpoint
  tasks of this session, then SETNX-then-INCR. SETNX makes recovery safe
  under concurrent callers — two requests racing on the same lost key
  both pin the same baseline.
- The recovery query MUST include in-flight tasks, otherwise queued/running
  tasks holding a reserved sequence would be re-issued the same number and
  collide on UNIQUE when the worker eventually writes.

Phase 1 accepts the millisecond-window race between Redis recovery and
the DB read — the failure mode is a UNIQUE-violation `failed` task that
the user can retry (planning/backend/task-queue.md §3.5 "殘餘 race").
"""

from __future__ import annotations

import logging
import uuid

from redis.asyncio import Redis
from sqlalchemy import Integer, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.checkpoint import Checkpoint
from app.models.task import Task

_logger = logging.getLogger(__name__)


def checkpoint_seq_key(session_id: uuid.UUID) -> str:
    """Redis key holding the next-checkpoint sequence cursor for a
    session. T-016 SETs this to 0 on session creation; T-017 INCRs to
    reserve the next value.
    """
    return f"seq:checkpoint:{session_id}"


async def _max_persisted_sequence(db: AsyncSession, session_id: uuid.UUID) -> int:
    stmt = select(func.coalesce(func.max(Checkpoint.sequence), 0)).where(
        Checkpoint.creation_session_id == session_id
    )
    result = await db.execute(stmt)
    return int(result.scalar_one())


async def _max_in_flight_sequence(db: AsyncSession, session_id: uuid.UUID) -> int:
    """Max `sequence` reserved by queued/running create_checkpoint tasks
    for this session. Must be included in the recovery baseline (planning
    §3.5) — otherwise a queued/running task holding sequence=N would let
    a recovery caller hand out N again, causing a UNIQUE collision when
    the worker eventually writes the checkpoint row.
    """
    stmt = select(
        func.coalesce(
            func.max(
                func.cast(Task.input_payload["sequence"].astext, Integer),
            ),
            0,
        )
    ).where(
        Task.task_type == "create_checkpoint",
        Task.status.in_(("queued", "running")),
        Task.input_payload["session_id"].astext == str(session_id),
    )
    try:
        result = await db.execute(stmt)
        return int(result.scalar_one())
    except Exception:  # noqa: BLE001 — recovery must degrade rather than fail closed
        _logger.exception("_max_in_flight_sequence: recovery query raised; falling back to 0")
        return 0


async def reserve_next_sequence(
    db: AsyncSession,
    redis: Redis,
    *,
    session_id: uuid.UUID,
) -> int:
    """Atomically reserve the next sequence value for a session.

    Two paths:
    - **Happy path**: T-016 seeded the key at 0. `INCR` is atomic and
      returns the next sequence directly.
    - **Recovery path**: Redis lost the key (eviction / restart). The
      caller's `INCR` would otherwise create the key at 0 and return 1
      — wrong if checkpoints already exist. We detect "key was missing"
      via `EXISTS` BEFORE the INCR, and if so, plant a recovery baseline
      via SETNX so concurrent recovery callers all converge on one value.

    `EXISTS` + `SETNX` + `INCR` is three round-trips, but the recovery
    path is rare (Redis is healthy in steady state) and the alternative
    — a Lua-scripted single-shot — would put the DB-derived baseline
    inside the Lua call, which can't query Postgres. The Lua approach
    would have to take the baseline as a parameter, which doesn't help
    correctness (a stale baseline parameter is the same race) and just
    adds complexity.
    """
    key = checkpoint_seq_key(session_id)

    exists = await redis.exists(key)
    if not exists:
        persisted = await _max_persisted_sequence(db, session_id)
        in_flight = await _max_in_flight_sequence(db, session_id)
        baseline = max(persisted, in_flight)
        # SETNX (not SET) — concurrent recovery callers race on this
        # write and only the first wins. Subsequent callers see a key
        # already present and proceed to INCR over the first caller's
        # baseline. Without SETNX, two recovery callers could both
        # SET=baseline and lose each other's INCR results.
        await redis.setnx(key, baseline)
        _logger.info(
            "reserve_next_sequence: recovered missing key %s with baseline=%d "
            "(persisted=%d, in_flight=%d)",
            key,
            baseline,
            persisted,
            in_flight,
        )

    incr_result = await redis.incr(key)
    return int(incr_result)


async def release_session_sequence(redis: Redis, session_id: uuid.UUID) -> None:
    """Drop the Redis key when a session reaches a terminal state.

    Best-effort: leaving the key around just costs a Redis byte until
    the next session starts; nothing depends on the DEL succeeding.
    """
    try:
        await redis.delete(checkpoint_seq_key(session_id))
    except Exception:  # noqa: BLE001 — advisory cleanup
        _logger.exception(
            "release_session_sequence: redis DEL failed for %s",
            session_id,
        )
