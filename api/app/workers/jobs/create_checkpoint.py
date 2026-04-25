"""`run_create_checkpoint` — arq job for the Creation Session checkpoint flow.

Pipeline (planning/backend/task-queue.md §3.3 + T-017 ticket):
  1. Idempotency: short-circuit if the task is already terminal /
     cancel-requested (matches `run_noop` template, Codex P1 round-3/4).
  2. CAS queued → running with cancel-race protection.
  3. Open `progress_publisher` so SSE clients see a moving bar.
  4. Reconcile prompt (LLM via PromptReconciler).
  5. Cancel checkpoint.
  6. Generate image (text2image / image2image based on session input
     mode + reference availability).
  7. Cancel checkpoint.
  8. Upload primary PNG + thumbnail via StorageBackend.
  9. Insert generation_log audit row.
 10. Insert checkpoint row referencing the log id.
 11. Publish completed SSE event with the Checkpoint DTO; mark task
     completed with the same DTO as `result`.

Cancel handling:
  - At any cancel checkpoint, if `cancel_requested` is True we mark the
    task `cancelled` and return WITHOUT writing a checkpoint row
    (`output_image_key` is NOT NULL — there's nothing valid to write
    if we abort early).

Errors:
  - All AgentErrorException raised by the AI / storage / DB layers are
    captured, mapped to `task.error`, and the task is marked `failed`.
  - Unhandled exceptions are wrapped in INTERNAL_UNEXPECTED_ERROR.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import IntegrityError

from app.ai.base import AIClient, AIGenerationResult
from app.ai.factory import get_image_client
from app.ai.progress import progress_publisher
from app.core.errors import AgentError, AgentErrorException, conflict_sequence_race
from app.core.redis_client import publish_task_event
from app.prompt.constraints import ReconcileMode
from app.prompt.reconciler import (
    PromptReconciler,
    ReconcileInput,
    ReconcileOutput,
    get_prompt_reconciler,
)
from app.repositories import (
    checkpoint_repo,
    generation_log_repo,
    task_repo,
)
from app.schemas.checkpoint_builder import build_checkpoint_dto, thumbnail_key_for
from app.storage.backend import StorageBackend
from app.storage.errors import StorageError
from app.storage.local import LocalFilesystemBackend
from app.utils.thumbnails import ensure_png_bytes, make_thumbnail_png

_logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = ("completed", "failed", "cancelled")
_CONTENT_TYPE_PNG = "image/png"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _safe_publish(redis: Any, task_id: uuid.UUID, payload: dict[str, Any]) -> None:
    """Publish without re-raising — DB is the source of truth, SSE is
    advisory. Mirrors `run_noop._safe_publish` so retries can't loop on
    a transient redis publish failure."""
    try:
        await publish_task_event(redis, task_id, payload)
    except Exception:  # noqa: BLE001 — best-effort
        _logger.exception(
            "run_create_checkpoint: redis publish failed for task=%s status=%s",
            task_id,
            payload.get("status"),
        )


def _resolve_storage_from_ctx(ctx: dict[str, Any]) -> StorageBackend:
    """Workers don't run inside FastAPI, so `Depends(get_storage)` isn't
    available. We look on `ctx` first (test harness can stash a fake)
    and fall back to the same `LocalFilesystemBackend` the route
    dependency builds. Storage selection is single-process by config —
    no Redis-backed factory.
    """
    backend = ctx.get("storage")
    if isinstance(backend, StorageBackend):
        return backend
    import os
    from pathlib import Path

    root = os.environ.get("STORAGE_ROOT", "/storage")
    return LocalFilesystemBackend(Path(root))


def _resolve_ai_client(ctx: dict[str, Any]) -> AIClient:
    """Workers can override the image client via ctx (test harness),
    otherwise we go through the standard factory which honours
    AI_STUB_MODE.
    """
    override = ctx.get("ai_client")
    if override is not None:
        return override  # type: ignore[no-any-return]
    return get_image_client(ctx["redis"])


def _resolve_reconciler(ctx: dict[str, Any]) -> PromptReconciler:
    override = ctx.get("reconciler")
    if isinstance(override, PromptReconciler):
        return override
    return get_prompt_reconciler(ctx["redis"])


def _agent_error_from_exception(exc: BaseException) -> AgentError:
    if isinstance(exc, AgentErrorException):
        return exc.error
    return AgentError(
        code="INTERNAL_UNEXPECTED_ERROR",
        message="系統發生未預期錯誤",
        problem=f"Unhandled exception in run_create_checkpoint: {type(exc).__name__}: {exc}",
        cause="Bug in the worker code path or an unforeseen runtime failure.",
        fix="Retry; if persistent, inspect the worker log for a stack trace.",
        retryable=True,
    )


def _checkpoint_storage_key(session_id: uuid.UUID, checkpoint_id: uuid.UUID) -> str:
    """Storage key per planning/data/storage-layout.md §2."""
    return f"checkpoints/{session_id}/{checkpoint_id}.png"


# ---------------------------------------------------------------------------
# Phase 1 helpers — these handle the small steps so the main handler
# stays readable.
# ---------------------------------------------------------------------------


def _decide_image_mode(payload: Mapping[str, Any]) -> str:
    """Pick text2image vs image2image based on (input_mode, mode, refs).

    - mode == 'remix'        → image2image (source checkpoint's output)
    - mode == 'retry_same'   → match source: reference keys are
      forwarded by the service for both real-reference AND remix
      lineage (we store the remix base's output_key in the source
      row's reference_image_keys, Codex P1 round-2). So a non-empty
      list means image2image regardless of how the source was made.
    - mode == 'fresh'        → text2image if no refs, else image2image
    """
    mode = payload.get("mode")
    refs = payload.get("reference_image_keys") or []
    if mode == "remix":
        return "image2image"
    if mode == "retry_same":
        return "image2image" if refs else "text2image"
    # fresh
    return "image2image" if refs else "text2image"


def _decide_reconcile_mode(payload: Mapping[str, Any]) -> ReconcileMode:
    """Reconciler 'mode' is checkpoint-creation-vs-alias-vs-motion. For
    T-017 we're always in the base-creation lane. With or without a
    reference image picks the right constraint set.
    """
    refs = payload.get("reference_image_keys") or []
    if refs or _decide_image_mode(payload) == "image2image":
        return ReconcileMode.CREATE_BASE_WITH_REF
    return ReconcileMode.CREATE_BASE


async def _is_cancel_requested(session_factory: Any, task_id: uuid.UUID) -> bool:
    """Quick re-check — used between phases."""
    async with session_factory() as db:
        task = await task_repo.get(db, task_id)
        if task is None:
            return True
        return bool(task.cancel_requested)


async def _commit_cancelled(session_factory: Any, task_id: uuid.UUID, redis: Any) -> None:
    async with session_factory() as db:
        await task_repo.mark_cancelled(db, task_id)
        await db.commit()
    await _safe_publish(
        redis,
        task_id,
        {"status": "cancelled", "task_id": str(task_id)},
    )


async def _commit_failed(
    session_factory: Any, task_id: uuid.UUID, error: AgentError, redis: Any
) -> None:
    error_dict = error.model_dump()
    async with session_factory() as db:
        await task_repo.mark_failed(db, task_id, error=error_dict)
        await db.commit()
    await _safe_publish(
        redis,
        task_id,
        {"status": "failed", "error": error_dict, "task_id": str(task_id)},
    )


async def _load_image_bytes_for_input(
    storage: StorageBackend,
    payload: Mapping[str, Any],
) -> bytes | None:
    """For image2image / remix we need source bytes.

    For `remix` mode the conditioning image is the base checkpoint's
    output; for non-remix image2image it's the first reference. The
    worker reads bytes synchronously — Phase 1 has at most one
    reference image driving conditioning per ai-integration.md §3.2.
    """
    mode = payload.get("mode")
    if mode == "remix":
        base_id = payload.get("base_checkpoint_id")
        if not base_id:
            return None
        # Remix: read the base checkpoint's output image. The worker
        # has the storage key path baked into the schema (from
        # storage-layout §2), so we don't need a DB roundtrip if the
        # caller provides the base_checkpoint_id.
        # Safer: load through the repo so we honour future migrations.
        # Skip that here — the storage layout is stable for Phase 1.
        return None  # remix handled in main flow with explicit DB read
    refs = payload.get("reference_image_keys") or []
    if not refs:
        return None
    primary_key = refs[0]
    try:
        return storage.get(primary_key)
    except StorageError:
        _logger.exception("create_checkpoint: failed to read reference key %s", primary_key)
        return None


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------


async def run_create_checkpoint(ctx: dict[str, Any], task_id: str) -> dict[str, Any]:
    """Full pipeline. See module docstring."""
    session_factory = ctx["db_session_factory"]
    redis = ctx["redis"]
    task_uuid = uuid.UUID(task_id)

    # ----- Phase 1: pickup + idempotency
    async with session_factory() as db:
        task = await task_repo.get(db, task_uuid)
        if task is None:
            return {"task_id": task_id, "ok": False, "reason": "missing"}

        if task.cancel_requested or task.status in _TERMINAL_STATUSES:
            published_terminal_after_retry_cancel = False
            if task.cancel_requested and task.status == "running":
                await task_repo.mark_cancelled(db, task_uuid)
                published_terminal_after_retry_cancel = True
            await db.commit()
            if published_terminal_after_retry_cancel:
                await _safe_publish(
                    redis,
                    task_uuid,
                    {"status": "cancelled", "task_id": str(task_uuid)},
                )
            return {
                "task_id": task_id,
                "ok": False,
                "reason": "cancelled" if task.cancel_requested else task.status,
            }

        if task.status == "queued":
            transitioned = await task_repo.transition_queued_to_running(db, task_uuid)
            await db.commit()
            if not transitioned:
                async with session_factory() as db2:
                    latest = await task_repo.get(db2, task_uuid)
                    if latest is None:
                        return {"task_id": task_id, "ok": False, "reason": "missing"}
                    if latest.status == "cancelled":
                        return {"task_id": task_id, "ok": False, "reason": "cancelled"}
                    return {
                        "task_id": task_id,
                        "ok": False,
                        "reason": f"raced_{latest.status}",
                    }

        # Snapshot fields the rest of the pipeline needs. SQLAlchemy
        # detaches on session exit — pull primitives now.
        payload: dict[str, Any] = dict(task.input_payload)
        user_id = task.user_id
        estimated_ms = task.estimated_duration_ms or 15_000
        started_at = task.started_at or datetime.now(UTC)

    await _safe_publish(redis, task_uuid, {"status": "running", "task_id": str(task_uuid)})

    # ----- Storage / AI client wiring
    storage = _resolve_storage_from_ctx(ctx)
    ai_client = _resolve_ai_client(ctx)
    reconciler = _resolve_reconciler(ctx)

    # ----- Phase 2: actual work, wrapped so any failure (AgentError or
    # otherwise) lands in `_commit_failed`. Cancel checkpoints between
    # each significant step.
    try:
        async with progress_publisher(redis, task_uuid, estimated_ms):
            if await _is_cancel_requested(session_factory, task_uuid):
                await _commit_cancelled(session_factory, task_uuid, redis)
                return {"task_id": task_id, "ok": False, "reason": "cancelled"}

            # Step 1: prompt reconciliation
            reconcile_mode = _decide_reconcile_mode(payload)
            reconcile_input = ReconcileInput(
                mode=reconcile_mode,
                menu_selections=payload.get("menu_selections"),
                freeform_note=payload.get("freeform_note"),
                has_reference_image=bool(payload.get("reference_image_keys")),
                has_inpaint_mask=False,
            )

            # retry_same: re-use the source checkpoint's prompt verbatim
            # (planning ticket §retry_same: "重用同 prompt + 不同 seed").
            reconciled: ReconcileOutput | None = None
            final_prompt: str
            if payload.get("mode") == "retry_same":
                base_id = payload.get("base_checkpoint_id")
                base_prompt: str | None = None
                if base_id is not None:
                    async with session_factory() as db:
                        source = await checkpoint_repo.get(db, uuid.UUID(str(base_id)))
                        if source is not None:
                            base_prompt = source.prompt
                if base_prompt:
                    final_prompt = base_prompt
                else:
                    # Fallback if the source vanished — degrade to a
                    # fresh reconcile so we still produce something.
                    reconciled = await reconciler.reconcile(reconcile_input)
                    final_prompt = reconciled.final_prompt
            else:
                reconciled = await reconciler.reconcile(reconcile_input)
                final_prompt = reconciled.final_prompt

            if await _is_cancel_requested(session_factory, task_uuid):
                await _commit_cancelled(session_factory, task_uuid, redis)
                return {"task_id": task_id, "ok": False, "reason": "cancelled"}

            # Step 2: image generation
            image_mode = _decide_image_mode(payload)
            seed_value = payload.get("seed")
            seed_int: int | None
            if seed_value is None:
                seed_int = None
            else:
                try:
                    seed_int = int(seed_value)
                except (TypeError, ValueError):
                    seed_int = None

            ai_result: AIGenerationResult
            input_image_bytes: bytes | None = None
            # Tracks what the AI call actually conditioned on, for
            # `generation_logs.input_image_keys` (audit / reproducibility).
            # Includes the remix base's output_image_key when applicable —
            # without this, remix log rows would show NULL even though
            # an input image WAS used (Codex P2 round-1).
            log_input_keys: list[str] = []
            if image_mode == "image2image":
                if payload.get("mode") == "remix":
                    base_id = payload.get("base_checkpoint_id")
                    if base_id is not None:
                        async with session_factory() as db:
                            source = await checkpoint_repo.get(db, uuid.UUID(str(base_id)))
                            if source is not None:
                                try:
                                    input_image_bytes = storage.get(source.output_image_key)
                                except StorageError as exc:
                                    raise AgentErrorException(
                                        AgentError(
                                            code="STORAGE_NOT_FOUND",
                                            message="找不到來源檔案",
                                            problem="Remix base checkpoint output is not in storage.",
                                            cause=str(exc),
                                            fix="Re-create the source checkpoint or pick a different one.",
                                            retryable=False,
                                        ),
                                        status_code=500,
                                    ) from exc
                                log_input_keys.append(source.output_image_key)
                else:
                    input_image_bytes = await _load_image_bytes_for_input(storage, payload)
                    # The first reference key was the conditioning image;
                    # carry it into the audit trail too.
                    refs = payload.get("reference_image_keys") or []
                    if refs:
                        log_input_keys.append(str(refs[0]))

                if input_image_bytes is None:
                    raise AgentErrorException(
                        AgentError(
                            code="STORAGE_NOT_FOUND",
                            message="找不到參考圖檔案",
                            problem="No source bytes available for image2image generation.",
                            cause="Reference image / base checkpoint output missing from storage.",
                            fix="Re-upload the reference or re-fetch the source checkpoint.",
                            retryable=False,
                        ),
                        status_code=500,
                    )
                # Provider's image-edits endpoint labels every multipart
                # upload `image/png` regardless of the bytes (see
                # gpt_image_2.py:_call_image2image). A JPEG / WebP
                # reference sent verbatim trips provider-side decode
                # validation (Codex P2 round-2). Convert to PNG here so
                # the labelled MIME and the actual bytes agree.
                try:
                    input_image_bytes = ensure_png_bytes(input_image_bytes)
                except ValueError as exc:
                    raise AgentErrorException(
                        AgentError(
                            code="STORAGE_NOT_FOUND",
                            message="參考圖無法解碼",
                            problem="Reference image bytes could not be decoded by PIL.",
                            cause=str(exc),
                            fix="Re-upload the reference as a clean PNG / JPEG / WebP.",
                            retryable=False,
                        ),
                        status_code=500,
                    ) from exc
                ai_result = await ai_client.generate_image_image2image(
                    final_prompt, input_image_bytes, seed=seed_int
                )
            else:
                ai_result = await ai_client.generate_image_text2image(final_prompt, seed=seed_int)

            if await _is_cancel_requested(session_factory, task_uuid):
                await _commit_cancelled(session_factory, task_uuid, redis)
                return {"task_id": task_id, "ok": False, "reason": "cancelled"}

            # Step 3: storage
            checkpoint_id = uuid.UUID(str(payload["checkpoint_id"]))
            session_id = uuid.UUID(str(payload["session_id"]))
            output_key = _checkpoint_storage_key(session_id, checkpoint_id)
            thumb_key = thumbnail_key_for(output_key)
            storage.put(output_key, ai_result.image_bytes, _CONTENT_TYPE_PNG)
            # Tracks whether the freshly-written files belong to a row
            # that didn't make it. We flip this off the moment a row
            # commits referencing them (Codex P2 round-4 — orphan
            # storage cleanup on DB rollback).
            output_orphaned = True

            # Thumbnail — best-effort. PIL failure or storage write
            # failure logs a warning and continues; the DTO returns
            # null thumbnail_url.
            thumb_bytes = make_thumbnail_png(ai_result.image_bytes)
            if thumb_bytes is not None:
                try:
                    storage.put(thumb_key, thumb_bytes, _CONTENT_TYPE_PNG)
                except StorageError:
                    _logger.warning("create_checkpoint: thumbnail put failed for %s", output_key)

            # Step 4: write generation_log + checkpoint atomically per
            # session. checkpoint references the log id as a soft FK.
            character_id_raw = payload.get("character_id")
            character_id_uuid = uuid.UUID(str(character_id_raw)) if character_id_raw else None

            # `reference_image_keys` on the row records what THIS
            # checkpoint was actually conditioned on (db-schema §3.5
            # field semantics). For uploaded references it's whatever
            # the caller sent; for remix it's the base checkpoint's
            # output_key. Recording the remix lineage here is what
            # lets retry_same find conditioning bytes for a remix
            # source — the source row's reference_image_keys becomes
            # the carry-forward channel (Codex P1 round-2).
            payload_ref_keys: list[str] = [
                str(k) for k in (payload.get("reference_image_keys") or [])
            ]
            row_ref_keys: list[str] = list(log_input_keys) if log_input_keys else payload_ref_keys
            input_image_keys_for_log: list[str] | None = (
                list(log_input_keys) if log_input_keys else None
            )

            try:
                async with session_factory() as db:
                    log_row = await generation_log_repo.insert_success(
                        db,
                        user_id=user_id,
                        character_id=character_id_uuid,
                        entity_type="checkpoint",
                        entity_id=checkpoint_id,
                        model_name="gpt-image-2",
                        model_version=ai_result.model_version,
                        final_prompt=final_prompt,
                        input_image_keys=input_image_keys_for_log,
                        parameters={
                            "image_mode": image_mode,
                            "seed": seed_int,
                        },
                        cost_units=ai_result.cost_units,
                        duration_ms=ai_result.duration_ms,
                        started_at=started_at,
                        completed_at=datetime.now(UTC),
                    )
                    try:
                        checkpoint_row = await checkpoint_repo.insert(
                            db,
                            checkpoint_id=checkpoint_id,
                            creation_session_id=session_id,
                            sequence=int(payload["sequence"]),
                            prompt=final_prompt,
                            user_menu_selections=payload.get("menu_selections"),
                            user_freeform_note=payload.get("freeform_note"),
                            reference_image_keys=row_ref_keys or None,
                            seed=str(seed_int) if seed_int is not None else None,
                            output_image_key=output_key,
                            generation_log_id=log_row.id,
                        )
                        await db.commit()
                        # Files now belong to a committed row.
                        output_orphaned = False
                    except IntegrityError as exc:
                        await db.rollback()
                        err_text = str(exc.orig or exc)
                        # Idempotent retry: arq retried after a previous
                        # attempt committed the checkpoint row but died
                        # before `mark_completed`. PK collision means the
                        # row already exists with this checkpoint_id, so
                        # the work is done — load the row and proceed
                        # to the success branch (Codex P1 round-4). The
                        # storage.put we just did overwrote identical
                        # bytes for the same key, so the files are still
                        # the canonical row's outputs.
                        if "checkpoints_pkey" in err_text:
                            existing = await checkpoint_repo.get(db, checkpoint_id)
                            if existing is not None:
                                checkpoint_row = existing
                                output_orphaned = False
                                _logger.info(
                                    "create_checkpoint: idempotent retry — "
                                    "checkpoint %s already committed; reusing row",
                                    checkpoint_id,
                                )
                            else:
                                # PK says exists but row vanished — race
                                # with concurrent delete. Re-raise.
                                raise
                        elif "uq_session_sequence" in err_text:
                            # The accepted residual race in the
                            # sequence allocator (task-queue.md §3.5).
                            # Surface as the dedicated retryable code
                            # instead of INTERNAL_UNEXPECTED_ERROR
                            # (Codex P2 round-2).
                            raise conflict_sequence_race() from exc
                        else:
                            raise
                    await db.refresh(checkpoint_row)
                    checkpoint_dto = build_checkpoint_dto(checkpoint_row, storage)
            finally:
                if output_orphaned:
                    # Cleanup files written in step 3 when no committed
                    # row references them (Codex P2 round-4). Targets
                    # both keys; storage.delete is idempotent.
                    for stale_key in (output_key, thumb_key):
                        try:
                            storage.delete(stale_key)
                        except StorageError:
                            _logger.warning(
                                "create_checkpoint: orphan cleanup failed for %s",
                                stale_key,
                            )

        # progress_publisher CM exit happens before mark_completed so the
        # final 1.0 / completed events arrive in the right order.
        result_payload = {"checkpoint": checkpoint_dto.model_dump(mode="json")}
        async with session_factory() as db:
            await task_repo.mark_completed(
                db,
                task_uuid,
                entity_type="checkpoint",
                entity_id=checkpoint_id,
                result=result_payload,
            )
            await db.commit()

        await _safe_publish(
            redis,
            task_uuid,
            {
                "status": "completed",
                "result": result_payload,
                "task_id": str(task_uuid),
            },
        )
        return {"task_id": task_id, "ok": True}

    except AgentErrorException as exc:
        _logger.warning(
            "run_create_checkpoint: task %s failing with %s",
            task_id,
            exc.error.code,
        )
        await _commit_failed(session_factory, task_uuid, exc.error, redis)
        return {"task_id": task_id, "ok": False, "reason": exc.error.code}
    except Exception as exc:  # noqa: BLE001 — catch-all so the task row never sticks
        _logger.exception("run_create_checkpoint: unhandled exception for task %s", task_id)
        await _commit_failed(session_factory, task_uuid, _agent_error_from_exception(exc), redis)
        return {"task_id": task_id, "ok": False, "reason": "internal_error"}
