"""`run_create_motion` — arq job for the motion generation flow (T-033).

Pipeline (planning/backend/task-queue.md §3.3 + T-033 ticket):
  1. Idempotency: short-circuit if the task is already terminal /
     cancel-requested. Mirrors `run_create_checkpoint` so retries
     and cancellations behave identically across task types.
  2. CAS queued → running with cancel-race protection.
  3. Cooperative cancel checkpoint.
  4. Resolve the prompt:
       - preset_*  → static template from `PRESET_MOTION_PROMPTS`,
         no LLM hop. Constraints prepended deterministically so the
         final string is "<motion constraints>. <preset prompt>." —
         identical shape to what T-035's preview emits, so the user
         sees the same prompt the worker uses.
       - custom    → reconciler with mode CREATE_MOTION (translate
         Chinese description, drop conflicts, compose constraints
         + reconciled note).
  5. Read parent image bytes from storage.
  6. Cancel checkpoint.
  7. Open `progress_publisher` so SSE clients see a moving bar.
  8. Call `VideoClient.generate_i2v` (Veo 3.1; identity-anchor
     handled inside the client per DECISIONS §3).
  9. Cancel checkpoint.
 10. Write video bytes + thumbnail (parent frame) to storage.
 11. Insert generation_log + motion atomically.
 12. mark_completed + emit completed SSE.

Cancel handling:
  - At any cancel checkpoint, if `cancel_requested` is True we mark
    the task `cancelled` and return WITHOUT writing a motion row.
    `motions.video_key` is NOT NULL so an aborted run produces
    nothing valid.

Errors:
  - All AgentErrorException raised by AI / storage / DB layers map to
    `task.error`, marking the task `failed`. Storage / generation
    cleanup is wrapped in `try/finally` so any orphaned files from a
    mid-pipeline crash get deleted.
  - Unhandled exceptions → INTERNAL_UNEXPECTED_ERROR (same envelope
    as `run_create_checkpoint._agent_error_from_exception`).

Idempotency:
  - The motion id is reserved at enqueue. If a previous attempt
    committed the row but crashed before mark_completed, the up-front
    short-circuit reuses the durable row and finalises the task on
    retry — exactly like `run_create_checkpoint`'s round-5 fix.
  - PK collision deeper in the pipeline (race against late pickup)
    falls into the same recovery branch.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy.exc import IntegrityError

from app.ai.base import VeoResult, VideoClient
from app.ai.factory import get_video_client
from app.ai.progress import progress_publisher
from app.core.errors import AgentError, AgentErrorException
from app.core.redis_client import publish_task_event
from app.prompt.constraints import ReconcileMode, get_constraints_for_mode
from app.prompt.errors import (
    conflict_motion_duplicate_name,
    conflict_motion_preset_already_exists,
)
from app.prompt.motion_templates import PRESET_MOTION_PROMPTS, PresetMotionType
from app.prompt.reconciler import (
    PromptReconciler,
    ReconcileInput,
    get_prompt_reconciler,
)
from app.repositories import (
    generation_log_repo,
    motion_repo,
    task_repo,
)
from app.schemas.motion_builder import build_motion_dto, thumbnail_key_for
from app.storage.backend import StorageBackend
from app.storage.errors import StorageError
from app.storage.local import LocalFilesystemBackend

_logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = ("completed", "failed", "cancelled")
_CONTENT_TYPE_MP4 = "video/mp4"
_CONTENT_TYPE_PNG = "image/png"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _safe_publish(redis: Any, task_id: uuid.UUID, payload: dict[str, Any]) -> None:
    """Publish without re-raising — DB is the source of truth, SSE is
    advisory. Mirrors `run_create_checkpoint._safe_publish`."""
    try:
        await publish_task_event(redis, task_id, payload)
    except Exception:  # noqa: BLE001 — best-effort
        _logger.exception(
            "run_create_motion: redis publish failed for task=%s status=%s",
            task_id,
            payload.get("status"),
        )


def _resolve_storage_from_ctx(ctx: dict[str, Any]) -> StorageBackend:
    backend = ctx.get("storage")
    if isinstance(backend, StorageBackend):
        return backend
    import os
    from pathlib import Path

    root = os.environ.get("STORAGE_ROOT", "/storage")
    return LocalFilesystemBackend(Path(root))


def _resolve_video_client(ctx: dict[str, Any]) -> VideoClient:
    """Workers can override the video client via ctx (test harness),
    otherwise we go through the standard factory which honours
    AI_STUB_MODE — same shape as `_resolve_ai_client` for image.

    `VideoClient` is `@runtime_checkable` so `isinstance` is the safer
    check; a test that ever stashes a wrong type on ctx surfaces
    immediately rather than crashing inside `generate_i2v` (Codex
    T-033 nit).
    """
    override = ctx.get("video_client")
    if isinstance(override, VideoClient):
        return override
    return get_video_client(ctx["redis"])


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
        problem=f"Unhandled exception in run_create_motion: {type(exc).__name__}: {exc}",
        cause="Bug in the worker code path or an unforeseen runtime failure.",
        fix="Retry; if persistent, inspect the worker log for a stack trace.",
        retryable=True,
    )


def _motion_storage_key(parent_type: str, parent_id: uuid.UUID, motion_id: uuid.UUID) -> str:
    """Storage key per T-033 ticket Notes.

    Diverges slightly from storage-layout §2's
    `characters/{character_id}/motions/{motion_id}.mp4` shape: the
    ticket mounts motions under their immediate parent (Base or Alias)
    rather than under the owning Character. Both are character-scoped
    transitively (every Base / Alias has exactly one character) but
    the ticket layout makes the polymorphic parent visible in the
    storage path itself, which matches the row schema better.
    Sprint-4 ZIP export will resolve via DB regardless of which path
    it walks.
    """
    if parent_type == "base":
        return f"bases/{parent_id}/motions/{motion_id}.mp4"
    if parent_type == "alias":
        return f"aliases/{parent_id}/motions/{motion_id}.mp4"
    raise ValueError(f"unknown parent_type: {parent_type!r}")


async def _is_cancel_requested(session_factory: Any, task_id: uuid.UUID) -> bool:
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


def _resolve_prompt_for_preset(motion_type: str) -> str:
    """Compose `<motion constraints>. <preset template>.` for a preset.

    Preset prompts skip the reconciler entirely (the templates are
    already English and align with the constraints). Composition
    matches `prompt_service.preview_create_motion`'s preset branch so
    the modal preview and the worker prompt agree byte-for-byte.
    """
    preset_type = cast(PresetMotionType, motion_type)
    constraints = get_constraints_for_mode(ReconcileMode.CREATE_MOTION)
    preset_prompt = PRESET_MOTION_PROMPTS[preset_type]
    return ", ".join(constraints) + ". " + preset_prompt + "."


async def _resolve_prompt_for_custom(reconciler: PromptReconciler, description: str) -> str:
    """Run the reconciler in motion mode for a custom description.

    `has_reference_image=True` because the parent image conditions Veo
    via the identity-anchor (planning §3 / Veo client) — the LLM should
    know it's writing for an i2v call so it stays compatible with the
    parent's pose / framing.
    """
    output = await reconciler.reconcile(
        ReconcileInput(
            mode=ReconcileMode.CREATE_MOTION,
            menu_selections=None,
            freeform_note=description,
            has_reference_image=True,
            has_inpaint_mask=False,
        )
    )
    return output.final_prompt


async def _existing_motion_for_payload(session_factory: Any, payload: Mapping[str, Any]) -> Any:
    """Return a committed motion row matching `payload['motion_id']`,
    or None. Used for the idempotent retry short-circuit (mirrors
    `run_create_checkpoint._existing_checkpoint_for_payload`).

    Uses `get_any` (NOT `get_active`) so a soft-deleted row from a
    previous attempt still finalises the task cleanly — without that,
    a retry between commit + soft-delete would re-run Veo, PK-collide,
    and surface as INTERNAL_UNEXPECTED_ERROR. See `motion_repo.get_any`
    for the full rationale.
    """
    raw = payload.get("motion_id")
    if not raw:
        return None
    try:
        mid = uuid.UUID(str(raw))
    except (TypeError, ValueError):
        return None
    async with session_factory() as db:
        return await motion_repo.get_any(db, mid)


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------


async def run_create_motion(ctx: dict[str, Any], task_id: str) -> dict[str, Any]:
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
            payload_snapshot: dict[str, Any] = dict(task.input_payload)
            existing = await _existing_motion_for_payload(session_factory, payload_snapshot)
            if existing is not None and task.status == "running" and task.entity_id is None:
                # Mirrors the checkpoint worker's "row durable but
                # task got a late cancel" recovery: finalise the
                # task as completed since the work IS done.
                storage_for_dto = _resolve_storage_from_ctx(ctx)
                dto = build_motion_dto(existing, storage_for_dto)
                result_payload = {"motion": dto.model_dump(mode="json")}
                async with session_factory() as db2:
                    await task_repo.mark_completed(
                        db2,
                        task_uuid,
                        entity_type="motion",
                        entity_id=existing.id,
                        result=result_payload,
                    )
                    await db2.commit()
                await _safe_publish(
                    redis,
                    task_uuid,
                    {
                        "status": "completed",
                        "result": result_payload,
                        "task_id": str(task_uuid),
                    },
                )
                _logger.info(
                    "create_motion: retry recovered committed row %s on cancelled task %s",
                    existing.id,
                    task_uuid,
                )
                return {"task_id": task_id, "ok": True, "reason": "recovered"}

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

        payload: dict[str, Any] = dict(task.input_payload)
        user_id = task.user_id
        estimated_ms = task.estimated_duration_ms or 60_000
        started_at = task.started_at or datetime.now(UTC)

    await _safe_publish(redis, task_uuid, {"status": "running", "task_id": str(task_uuid)})

    # ----- Storage / AI client wiring
    storage = _resolve_storage_from_ctx(ctx)
    video_client = _resolve_video_client(ctx)
    reconciler = _resolve_reconciler(ctx)

    # ----- Phase 1.5: idempotent retry short-circuit. If a prior
    # attempt committed the motion row but crashed before
    # mark_completed, finalise the task here and skip the (expensive)
    # Veo call. PK collision deeper in the pipeline handles late
    # races; this is the cheap up-front path.
    existing_row = await _existing_motion_for_payload(session_factory, payload)
    if existing_row is not None:
        dto = build_motion_dto(existing_row, storage)
        result_payload = {"motion": dto.model_dump(mode="json")}
        async with session_factory() as db:
            await task_repo.mark_completed(
                db,
                task_uuid,
                entity_type="motion",
                entity_id=existing_row.id,
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
        _logger.info(
            "create_motion: retry skip-Veo; row %s already exists for task %s",
            existing_row.id,
            task_uuid,
        )
        return {"task_id": task_id, "ok": True, "reason": "already_committed"}

    # ----- Phase 2: real work.
    motion_committed = False
    motion_id = uuid.UUID(str(payload["motion_id"]))
    parent_type = str(payload["parent_type"])
    parent_id = uuid.UUID(str(payload["parent_id"]))
    parent_image_key = str(payload["parent_image_key"])
    motion_type = str(payload["motion_type"])
    motion_name = str(payload["name"])
    description: str | None = (
        str(payload["description"]) if payload.get("description") is not None else None
    )
    character_id_raw = payload.get("character_id")
    character_id_uuid = uuid.UUID(str(character_id_raw)) if character_id_raw else None
    video_key = _motion_storage_key(parent_type, parent_id, motion_id)
    thumb_key = thumbnail_key_for(video_key)

    try:
        if await _is_cancel_requested(session_factory, task_uuid):
            await _commit_cancelled(session_factory, task_uuid, redis)
            return {"task_id": task_id, "ok": False, "reason": "cancelled"}

        # Step 1: prompt resolution. Preset short-circuits the LLM;
        # custom goes through the reconciler.
        if motion_type == "custom":
            if not description:
                # Service layer already enforces this; defensive guard
                # in case a stale task row sneaks through.
                raise AgentErrorException(
                    AgentError(
                        code="VALIDATION_MOTION_DESCRIPTION_REQUIRED",
                        message="自訂動作必須填寫描述",
                        problem="Custom motion task payload is missing `description`.",
                        cause="Service layer normally rejects this; the task "
                        "row may predate that validation or was manually inserted.",
                        fix="Re-create the motion via the API; the service "
                        "layer will reject the bad payload at enqueue time.",
                        retryable=False,
                    ),
                    status_code=422,
                )
            final_prompt = await _resolve_prompt_for_custom(reconciler, description)
        else:
            final_prompt = _resolve_prompt_for_preset(motion_type)

        if await _is_cancel_requested(session_factory, task_uuid):
            await _commit_cancelled(session_factory, task_uuid, redis)
            return {"task_id": task_id, "ok": False, "reason": "cancelled"}

        # Step 2: read parent image bytes. Failure here is a structured
        # STORAGE_NOT_FOUND so ops triage can tell it apart from a Veo
        # miss.
        try:
            parent_image_bytes = storage.get(parent_image_key)
        except StorageError as exc:
            raise AgentErrorException(
                AgentError(
                    code="STORAGE_NOT_FOUND",
                    message="找不到來源圖檔",
                    problem="Motion parent image could not be read from storage.",
                    cause=str(exc),
                    fix="Re-fetch the parent (Base or Alias) and regenerate the motion.",
                    retryable=False,
                ),
                status_code=500,
            ) from exc

        if await _is_cancel_requested(session_factory, task_uuid):
            await _commit_cancelled(session_factory, task_uuid, redis)
            return {"task_id": task_id, "ok": False, "reason": "cancelled"}

        # Step 3: Veo i2v call wrapped in the progress publisher so
        # SSE consumers see a moving bar during the (~30-120s) call.
        async with progress_publisher(redis, task_uuid, estimated_ms):
            veo_result: VeoResult = await video_client.generate_i2v(
                image_bytes=parent_image_bytes,
                prompt=final_prompt,
            )

            if await _is_cancel_requested(session_factory, task_uuid):
                await _commit_cancelled(session_factory, task_uuid, redis)
                return {"task_id": task_id, "ok": False, "reason": "cancelled"}

            # Step 4: storage write. The video bytes are the source of
            # truth; the thumbnail is best-effort. For Phase 1 we use
            # the parent image as the thumbnail — the identity-anchor
            # in Veo (DECISIONS §3) makes the first frame visually
            # near-identical to the parent, so re-encoding the parent
            # bytes via PIL avoids pulling in a heavy ffmpeg dep just
            # to extract a frame. The ticket's `imageio[ffmpeg]`
            # alternative is documented but deferred — if the
            # difference ever matters, swap the body of
            # `_thumbnail_bytes_for_motion` and the call site stays
            # unchanged.
            storage.put(video_key, veo_result.video_bytes, _CONTENT_TYPE_MP4)
            video_orphaned = True

            thumb_bytes = _thumbnail_bytes_for_motion(parent_image_bytes)
            if thumb_bytes is not None:
                try:
                    storage.put(thumb_key, thumb_bytes, _CONTENT_TYPE_PNG)
                except StorageError:
                    _logger.warning("create_motion: thumbnail put failed for %s", video_key)

            # Step 5: write generation_log + motion atomically.
            try:
                async with session_factory() as db:
                    log_row = await generation_log_repo.insert_success(
                        db,
                        user_id=user_id,
                        character_id=character_id_uuid,
                        entity_type="motion",
                        entity_id=motion_id,
                        model_name="veo-3.1",
                        model_version=veo_result.model_version,
                        final_prompt=final_prompt,
                        input_image_keys=[parent_image_key],
                        parameters={
                            "motion_type": motion_type,
                            **veo_result.generation_log_payload,
                        },
                        # Per ai-integration §6: 1 Veo call ≈ 10 cost
                        # units. Keep this static for Phase 1; a future
                        # ticket can derive from `generation_log_payload`
                        # when Veo starts surfacing real per-call cost.
                        cost_units=10.0,
                        duration_ms=veo_result.duration_ms,
                        started_at=started_at,
                        completed_at=datetime.now(UTC),
                    )
                    try:
                        motion_row = await motion_repo.insert(
                            db,
                            motion_id=motion_id,
                            parent_type=parent_type,
                            parent_id=parent_id,
                            motion_type=motion_type,
                            name=motion_name,
                            description=description,
                            video_key=video_key,
                            duration_ms=veo_result.duration_ms or None,
                            generation_log_id=log_row.id,
                        )
                        await db.commit()
                        video_orphaned = False
                        motion_committed = True
                    except IntegrityError as exc:
                        await db.rollback()
                        err_text = str(exc.orig or exc)
                        # Idempotent retry recovery: a previous attempt
                        # committed the row but the worker died before
                        # mark_completed. Use `get_any` so a soft-
                        # deleted row from a stale retry still finalises
                        # the task cleanly (Codex review on T-033).
                        if "motions_pkey" in err_text:
                            existing = await motion_repo.get_any(db, motion_id)
                            if existing is not None:
                                motion_row = existing
                                video_orphaned = False
                                motion_committed = True
                                _logger.info(
                                    "create_motion: idempotent retry — "
                                    "motion %s already committed; reusing row",
                                    motion_id,
                                )
                            else:
                                raise
                        elif (
                            "uq_motions_base_name" in err_text
                            or "uq_motions_alias_name" in err_text
                        ):
                            # A concurrent racer slipped in between the
                            # service layer's pre-check and our INSERT.
                            # Surface as the same 409 the service uses
                            # so the response code is consistent
                            # whether the conflict is detected pre- or
                            # post-task.
                            raise conflict_motion_duplicate_name() from exc
                        elif (
                            "uq_motions_base_motion_type" in err_text
                            or "uq_motions_alias_motion_type" in err_text
                        ):
                            # Preset-slot uniqueness — F-20's "5 fixed
                            # slots per parent" enforced by partial
                            # UNIQUE (migration 20260430_015). Catches
                            # the TOCTOU window between
                            # `find_active_preset_for_parent` and the
                            # worker's INSERT (Codex T-033 P2 review).
                            #
                            # Note: `chk_motions_type` is intentionally
                            # NOT in this branch — that CHECK catches
                            # malformed `motion_type`, not a preset-
                            # slot collision; mapping it to
                            # `CONFLICT_PRESET_ALREADY_EXISTS` would
                            # mislead ops triage. It falls through to
                            # `raise` below as INTERNAL.
                            raise conflict_motion_preset_already_exists() from exc
                        else:
                            raise

                    try:
                        await db.refresh(motion_row)
                    except Exception:  # noqa: BLE001 — recoverable post-commit
                        _logger.warning(
                            "create_motion: post-commit refresh failed for motion %s; "
                            "recovering via fresh session",
                            motion_id,
                            exc_info=True,
                        )
            finally:
                if video_orphaned:
                    for stale_key in (video_key, thumb_key):
                        try:
                            storage.delete(stale_key)
                        except StorageError:
                            _logger.warning(
                                "create_motion: orphan cleanup failed for %s",
                                stale_key,
                            )

        # Build the DTO post-commit (mirrors run_create_checkpoint's
        # round-11 fix). Row is durable; if DTO build hiccups we fall
        # back to a minimal payload rather than marking the task
        # failed.
        try:
            motion_dto = build_motion_dto(motion_row, storage)
        except Exception:  # noqa: BLE001 — DTO is recoverable post-commit
            _logger.warning(
                "create_motion: in-session DTO build failed; re-reading",
                exc_info=True,
            )
            try:
                async with session_factory() as db_read:
                    reread = await motion_repo.get_active(db_read, motion_id)
                if reread is not None:
                    motion_dto = build_motion_dto(reread, storage)
                else:
                    raise RuntimeError("motion vanished post-commit")
            except Exception:  # noqa: BLE001 — last-resort minimal payload
                _logger.exception("create_motion: DTO recovery failed; using minimal payload")
                motion_dto = None

        if motion_dto is not None:
            result_payload = {"motion": motion_dto.model_dump(mode="json")}
        else:
            result_payload = {
                "motion": {
                    "id": str(motion_id),
                    "parent": {"type": parent_type, "id": str(parent_id)},
                }
            }

        # Post-commit task finalisation. motion_committed=True so any
        # exception here re-raises (rather than _commit_failed) and
        # arq retries; the next attempt's idempotency lookup picks
        # up the durable motion row and finalises cleanly. Same
        # rationale as run_create_checkpoint round-14.
        async with session_factory() as db:
            await task_repo.mark_completed(
                db,
                task_uuid,
                entity_type="motion",
                entity_id=motion_id,
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
        if motion_committed:
            _logger.warning(
                "run_create_motion: post-commit AgentError for task %s; "
                "raising for arq retry. code=%s",
                task_id,
                exc.error.code,
            )
            raise
        _logger.warning(
            "run_create_motion: task %s failing with %s",
            task_id,
            exc.error.code,
        )
        await _commit_failed(session_factory, task_uuid, exc.error, redis)
        return {"task_id": task_id, "ok": False, "reason": exc.error.code}
    except Exception as exc:  # noqa: BLE001 — catch-all so the task row never sticks
        if motion_committed:
            _logger.warning(
                "run_create_motion: post-commit failure for task %s; raising for arq retry",
                task_id,
                exc_info=True,
            )
            raise
        _logger.exception("run_create_motion: unhandled exception for task %s", task_id)
        await _commit_failed(session_factory, task_uuid, _agent_error_from_exception(exc), redis)
        return {"task_id": task_id, "ok": False, "reason": "internal_error"}


def _thumbnail_bytes_for_motion(parent_image_bytes: bytes) -> bytes | None:
    """Return PNG bytes for the motion thumbnail.

    Phase 1 strategy: re-encode the parent (Base / Alias) image as a
    smaller PNG. The Veo identity-anchor (DECISIONS §3) sends the
    parent as both first and last frame, so the actual first video
    frame is visually near-identical to the parent — re-using the
    parent bytes here avoids an `imageio[ffmpeg]` dep + an actual
    ffmpeg invocation per task while still producing a representative
    thumbnail.

    Returns None if PIL can't decode the parent (no thumbnail; the
    DTO will surface `thumbnail_url=null`).
    """
    from app.utils.thumbnails import make_thumbnail_png

    return make_thumbnail_png(parent_image_bytes)
