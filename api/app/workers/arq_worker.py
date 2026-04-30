"""arq `WorkerSettings` — entry point for the `arq` CLI process.

Run via `arq app.workers.arq_worker.WorkerSettings` (see
docker-compose `worker` service). The settings object is also imported
by tests that drive a worker in-process.

`on_startup` builds a per-process async DB session factory and Redis
client; both are stashed on `ctx` so job handlers don't have to redo
that wiring on every job. This is the standard arq pattern — handlers
receive `ctx` (a dict) as their first argument.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from arq import cron
from arq.connections import RedisSettings
from arq.worker import func

from app.core.redis_client import arq_redis_settings, get_redis
from app.db.session import async_session_factory
from app.workers.jobs.cleanup import cleanup_terminal_tasks
from app.workers.jobs.create_alias import run_create_alias
from app.workers.jobs.create_checkpoint import run_create_checkpoint
from app.workers.jobs.create_motion import run_create_motion
from app.workers.jobs.noop import run_noop

# Per-function timeout for the motion worker (Codex T-033 P1 round-5/6).
# The pool-wide default `job_timeout=300s` cancels long-but-valid
# generations mid-flight; arq's CancelledError raises outside the
# worker's `except Exception` path and leaves the task row stuck
# `running`.
#
# Worst-case Veo i2v budget (round-6 includes submit retries):
#   - submit retries:  (VEO_MAX_RETRIES=2 default) → 3 attempts ×
#                      VEO_TIMEOUT_MS=180s + 1s + 2s exponential
#                      backoff between attempts ≈ 543s. The
#                      `_submit_with_retry` envelope in `Veo31Client`
#                      only protects this step; the per-attempt
#                      timeout is the same `VEO_TIMEOUT_MS`.
#   - poll:            VEO_MAX_POLL_ATTEMPTS=60 × VEO_POLL_INTERVAL_MS=5s
#                      = 300s. No retry envelope post-submit.
#   - download:        ≤ VEO_TIMEOUT_MS = 180s. Single shot.
#   - storage + DB:    storage put + copy + INSERT + commit ≈ 30s
#                      headroom on a healthy backend.
# Total: ≈ 1053s before timeout pressure.
#
# 1200s gives ~150s additional headroom and keeps a single round
# number for ops dashboards. The pool default stays tight at 300s for
# noop / checkpoint / cleanup so a runaway in those paths is still
# bounded — only the motion worker gets the extended budget where Veo
# actually needs it.
_MOTION_JOB_TIMEOUT_SECONDS = 1200

_logger = logging.getLogger(__name__)


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        _logger.warning("env %s=%r is not an int; falling back to %d", name, raw, default)
        return default
    return value if value > 0 else default


async def on_startup(ctx: dict[str, Any]) -> None:
    """Attach shared resources to the worker context.

    `db_session_factory` is a callable returning an `AsyncSession`
    context manager — handlers `async with session_factory() as db:`
    rather than receiving a session directly so each unit-of-work owns
    its commit/rollback semantics.
    """
    ctx["db_session_factory"] = async_session_factory()
    ctx["redis"] = await get_redis()
    _logger.info("arq worker started")


async def on_shutdown(ctx: dict[str, Any]) -> None:  # noqa: ARG001 — arq signature
    _logger.info("arq worker shutting down")


def _resolve_redis_settings() -> RedisSettings:
    """Resolve arq Redis settings at import time, but tolerate missing
    `REDIS_URL` so tests / docs builds can import this module without a
    live Redis. The worker process always has REDIS_URL set in
    docker-compose."""
    try:
        return arq_redis_settings()
    except RuntimeError:
        _logger.warning("REDIS_URL not set at import; arq WorkerSettings using defaults")
        return RedisSettings()


class WorkerSettings:
    """arq settings consumed by the `arq` CLI."""

    redis_settings: RedisSettings = _resolve_redis_settings()

    functions = [
        run_noop,
        run_create_checkpoint,
        run_create_alias,
        func(run_create_motion, timeout=_MOTION_JOB_TIMEOUT_SECONDS),
    ]

    # Run every hour on the minute. `minute={0}` is the supported way to
    # schedule a single firing per hour in arq.cron.
    cron_jobs = [
        cron(
            cleanup_terminal_tasks,
            minute={0},
            run_at_startup=False,
        ),
    ]

    on_startup = on_startup
    on_shutdown = on_shutdown

    max_jobs: int = _int_env("WORKER_CONCURRENCY", 4)
    max_tries: int = 3
    job_timeout: int = 300  # 5min hard ceiling for any single job
