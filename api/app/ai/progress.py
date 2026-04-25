"""`progress_publisher` — emit `running` SSE events on a fixed cadence (T-014).

Workers wrap their AI calls with this CM. While the body runs, a background
task pushes one `running` event every `interval_seconds` (default 2s) onto
the task channel, with a progress estimate derived from elapsed/estimated.

Capping at 0.95 keeps the bar from claiming completion before the actual
write commits — the worker (or job runner) emits the final
`running progress=1.0` or `completed` after work finishes.

The CM cancels its background task in `__aexit__` and awaits it so callers
never leak coroutines; the task swallows `CancelledError` cleanly so a
short-lived AI call (< interval) doesn't surface a spurious traceback.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from redis.asyncio import Redis

from app.core.redis_client import publish_task_event

_logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL_SECONDS = 2.0
_PROGRESS_CEIL = 0.95


@asynccontextmanager
async def progress_publisher(
    redis: Redis,
    task_id: uuid.UUID | str,
    estimated_ms: int,
    *,
    interval_seconds: float = _DEFAULT_INTERVAL_SECONDS,
) -> AsyncIterator[None]:
    """Async CM that publishes estimated progress every `interval_seconds`.

    `estimated_ms` is the worker's best guess at total runtime; progress is
    `min(elapsed_ms / estimated_ms, 0.95)`. Treat as advisory — drift is
    fine because the SSE consumer renders a moving bar, not an exact %.
    """
    if estimated_ms <= 0:
        estimated_ms = 1  # avoid div-by-zero; progress will saturate at 0.95 fast

    cancel_event = asyncio.Event()
    started_at = asyncio.get_running_loop().time()

    async def _loop() -> None:
        try:
            while not cancel_event.is_set():
                try:
                    await asyncio.wait_for(cancel_event.wait(), timeout=interval_seconds)
                    return  # cancellation while waiting — exit cleanly
                except TimeoutError:
                    pass
                elapsed_ms = (asyncio.get_running_loop().time() - started_at) * 1000.0
                progress = min(elapsed_ms / estimated_ms, _PROGRESS_CEIL)
                payload: dict[str, Any] = {
                    "status": "running",
                    "progress": round(progress, 4),
                    "task_id": str(task_id),
                }
                try:
                    await publish_task_event(redis, task_id, payload)
                except Exception:  # noqa: BLE001 — publishing is advisory
                    _logger.exception("progress_publisher: publish failed for task=%s", task_id)
        except asyncio.CancelledError:
            # Cooperative cancel from the parent task — swallow so the
            # CM's __aexit__ can complete without re-raising into the
            # caller's `with` block.
            return

    task = asyncio.create_task(_loop(), name=f"progress-publisher:{task_id}")
    try:
        yield
    finally:
        cancel_event.set()
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            # Already swallowed inside _loop, but belt-and-suspenders for the
            # case where cancel arrives before the inner try caught it.
            pass
