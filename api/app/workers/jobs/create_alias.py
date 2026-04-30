"""`run_create_alias` — arq job for the alias generation flow (T-031).

Pipeline (planning ticket T-031 §Scope):
  1. Idempotency: short-circuit if the task is already terminal /
     cancel-requested. Mirrors `run_create_checkpoint`.
  2. CAS queued → running with cancel-race protection.
  3. Open `progress_publisher` so SSE clients see a moving bar.
  4. Reconcile prompt (`CREATE_ALIAS` mode).
  5. Cancel checkpoint.
  6. Read base image bytes (always); reference image bytes (if any);
     mask bytes (if any). Normalize to PNG via `ensure_png_bytes` so
     the AI client's hard-labelled `image/png` multipart stays honest.
  7. Cancel checkpoint.
  8. Dispatch to T-030 edit method:
       - `inpaint` (or `mixed` with mask) → `edit_inpaint`
       - `image` / `mixed` with refs only → `edit_image2image`
       - `text` → `edit_image2image` with empty references (the
         freeform note alone drives the variation against the base)
  9. Cancel checkpoint.
 10. Write alias PNG + thumbnail to storage (`aliases/{alias_id}.png`).
 11. Insert generation_log row, then alias row referencing the log id.
 12. Publish completed SSE event with the AliasDTO; mark task completed
     with the same DTO as `result`.

Cancel handling:
  - At any cancel checkpoint, mark the task `cancelled` and return
    WITHOUT writing an alias row. Files written in step 10 are cleaned
    up by an `output_orphaned` flag — same pattern as
    `run_create_checkpoint`.

Idempotency:
  - Up-front: if an alias row with the reserved id already exists,
    finalize the task without re-running the AI call.
  - Mid-write: PK collision on INSERT (because a previous attempt
    committed but crashed before mark_completed) is caught and
    treated as success.
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
from app.core.errors import AgentError, AgentErrorException
from app.core.redis_client import publish_task_event
from app.prompt.constraints import ReconcileMode
from app.prompt.reconciler import (
    PromptReconciler,
    ReconcileInput,
    ReconcileOutput,
    get_prompt_reconciler,
)
from app.repositories import (
    alias_repo,
    generation_log_repo,
    task_repo,
)
from app.schemas.alias_builder import build_alias_dto
from app.schemas.checkpoint_builder import thumbnail_key_for
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
    """Publish without re-raising. DB is the source of truth; SSE is
    advisory. Mirrors `run_create_checkpoint._safe_publish`."""
    try:
        await publish_task_event(redis, task_id, payload)
    except Exception:  # noqa: BLE001 — best-effort
        _logger.exception(
            "run_create_alias: redis publish failed for task=%s status=%s",
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


def _resolve_ai_client(ctx: dict[str, Any]) -> AIClient:
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
        problem=f"Unhandled exception in run_create_alias: {type(exc).__name__}: {exc}",
        cause="Bug in the worker code path or an unforeseen runtime failure.",
        fix="Retry; if persistent, inspect the worker log for a stack trace.",
        retryable=True,
    )


def _alias_storage_key(alias_id: uuid.UUID) -> str:
    """Per planning/data/storage-layout.md §2 (the character_id in the
    key path is dropped because aliases live globally under
    `aliases/{alias_id}.png`; the row's character_id provides scoping)."""
    return f"aliases/{alias_id}.png"


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


def _model_input_mode_for(payload_mode: str) -> str:
    """Map the wire `input_mode` (text/image/inpaint/mixed) to the
    persisted `aliases.input_mode` (image2image/inpaint/text2image/mixed).

    The DB CHECK constraint accepts the persisted set; the wire surface
    uses the user-facing set per api-shape §5.3 + product semantics.
    """
    return {
        "text": "text2image",
        "image": "image2image",
        "inpaint": "inpaint",
        "mixed": "mixed",
    }[payload_mode]


def _decide_dispatch(payload: Mapping[str, Any]) -> str:
    """Pick the T-030 method to call based on the payload.

    - mask present → `edit_inpaint`
    - else         → `edit_image2image` (refs may be empty for `text`)
    """
    if payload.get("mask_key"):
        return "edit_inpaint"
    return "edit_image2image"


async def _existing_alias_for_payload(session_factory: Any, payload: Mapping[str, Any]) -> Any:
    """Return a committed alias row matching `payload['alias_id']`, or
    None. Used to short-circuit retries on idempotent reruns."""
    raw = payload.get("alias_id")
    if not raw:
        return None
    try:
        aid = uuid.UUID(str(raw))
    except (TypeError, ValueError):
        return None
    async with session_factory() as db:
        return await alias_repo.get_active(db, aid)


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------


async def run_create_alias(ctx: dict[str, Any], task_id: str) -> dict[str, Any]:
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
            # If a prior attempt committed the alias row but crashed
            # before mark_completed, AND the user then cancelled, we'd
            # reach this branch with cancel_requested=True + status=running.
            # Marking the task `cancelled` would orphan the committed row.
            # Recognize the work IS done and finalize as `completed` —
            # semantically a too-late cancel (mirrors create_checkpoint
            # P2 round-5).
            payload_snapshot: dict[str, Any] = dict(task.input_payload)
            existing = await _existing_alias_for_payload(session_factory, payload_snapshot)
            if existing is not None and task.status == "running" and task.entity_id is None:
                storage_for_dto = _resolve_storage_from_ctx(ctx)
                dto = build_alias_dto(existing, storage_for_dto)
                result_payload = {"alias": dto.model_dump(mode="json")}
                async with session_factory() as db2:
                    await task_repo.mark_completed(
                        db2,
                        task_uuid,
                        entity_type="alias",
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
                    "create_alias: retry recovered committed row %s on cancelled task %s",
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
        estimated_ms = task.estimated_duration_ms or 20_000
        started_at = task.started_at or datetime.now(UTC)

    await _safe_publish(redis, task_uuid, {"status": "running", "task_id": str(task_uuid)})

    # ----- Storage / AI client wiring
    storage = _resolve_storage_from_ctx(ctx)
    ai_client = _resolve_ai_client(ctx)
    reconciler = _resolve_reconciler(ctx)

    # ----- Phase 1.5: idempotent retry short-circuit. If a prior attempt
    # already committed the alias row but crashed before mark_completed,
    # skip the AI call (cost + time) and just finalize the task.
    existing_row = await _existing_alias_for_payload(session_factory, payload)
    if existing_row is not None:
        dto = build_alias_dto(existing_row, storage)
        result_payload = {"alias": dto.model_dump(mode="json")}
        async with session_factory() as db:
            await task_repo.mark_completed(
                db,
                task_uuid,
                entity_type="alias",
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
            "create_alias: retry skip-AI; row %s already exists for task %s",
            existing_row.id,
            task_uuid,
        )
        return {"task_id": task_id, "ok": True, "reason": "already_committed"}

    alias_id = uuid.UUID(str(payload["alias_id"]))
    character_id = uuid.UUID(str(payload["character_id"]))
    output_key = _alias_storage_key(alias_id)
    thumb_key = thumbnail_key_for(output_key)

    # ----- Phase 2: actual work. `alias_committed` tracks whether the
    # row has been durably persisted; post-commit failures must
    # propagate to arq so the retry's up-front idempotency lookup can
    # finalize cleanly (mirrors create_checkpoint Codex P1 round-14).
    alias_committed = False
    try:
        async with progress_publisher(redis, task_uuid, estimated_ms):
            if await _is_cancel_requested(session_factory, task_uuid):
                await _commit_cancelled(session_factory, task_uuid, redis)
                return {"task_id": task_id, "ok": False, "reason": "cancelled"}

            # Step 1: prompt reconciliation. Always CREATE_ALIAS mode —
            # the reconciler treats the base as already-compliant and
            # only emits the user's change request (no re-injection of
            # transparent_bg / centered, which would confuse the edit).
            has_refs = bool(payload.get("reference_image_keys"))
            has_mask = bool(payload.get("mask_key"))
            reconcile_input = ReconcileInput(
                mode=ReconcileMode.CREATE_ALIAS,
                menu_selections=None,
                freeform_note=payload.get("freeform_note"),
                has_reference_image=has_refs,
                has_inpaint_mask=has_mask,
            )
            reconciled: ReconcileOutput = await reconciler.reconcile(reconcile_input)
            final_prompt = reconciled.final_prompt

            if await _is_cancel_requested(session_factory, task_uuid):
                await _commit_cancelled(session_factory, task_uuid, redis)
                return {"task_id": task_id, "ok": False, "reason": "cancelled"}

            # Step 2: load base + references + mask bytes from storage.
            base_image_key = str(payload["base_image_key"])
            log_input_keys: list[str] = [base_image_key]
            try:
                base_bytes = storage.get(base_image_key)
            except StorageError as exc:
                raise AgentErrorException(
                    AgentError(
                        code="STORAGE_NOT_FOUND",
                        message="找不到基礎形象圖檔",
                        problem="Base image is not in storage.",
                        cause=str(exc),
                        fix="Re-confirm the character's Base; if the file is "
                        "permanently lost, the character must be re-created.",
                        retryable=False,
                    ),
                    status_code=500,
                ) from exc
            try:
                base_bytes = ensure_png_bytes(base_bytes)
            except ValueError as exc:
                raise AgentErrorException(
                    AgentError(
                        code="VALIDATION_REFERENCE_IMAGE_UNDECODABLE",
                        message="基礎形象圖檔無法解碼",
                        problem="Base image bytes could not be decoded by PIL.",
                        cause=str(exc),
                        fix="Re-confirm the character's Base.",
                        retryable=False,
                    ),
                    status_code=500,
                ) from exc

            reference_image_bytes: list[bytes] = []
            ref_keys = payload.get("reference_image_keys") or []
            for ref_key in ref_keys:
                try:
                    ref_raw = storage.get(str(ref_key))
                except StorageError as exc:
                    raise AgentErrorException(
                        AgentError(
                            code="STORAGE_NOT_FOUND",
                            message="找不到參考圖檔案",
                            problem=f"Reference image at {ref_key} is not in storage.",
                            cause=str(exc),
                            fix="Re-upload the reference and retry.",
                            retryable=False,
                        ),
                        status_code=500,
                    ) from exc
                try:
                    reference_image_bytes.append(ensure_png_bytes(ref_raw))
                except ValueError as exc:
                    raise AgentErrorException(
                        AgentError(
                            code="VALIDATION_REFERENCE_IMAGE_UNDECODABLE",
                            message="參考圖無法解碼",
                            problem="Reference image bytes could not be decoded by PIL.",
                            cause=str(exc),
                            fix="Re-upload the reference as a clean PNG / JPEG / WebP.",
                            retryable=False,
                        ),
                        status_code=500,
                    ) from exc
                log_input_keys.append(str(ref_key))

            mask_bytes: bytes | None = None
            mask_key_raw = payload.get("mask_key")
            if mask_key_raw:
                try:
                    mask_bytes = storage.get(str(mask_key_raw))
                except StorageError as exc:
                    raise AgentErrorException(
                        AgentError(
                            code="STORAGE_NOT_FOUND",
                            message="找不到遮罩檔案",
                            problem=f"Mask at {mask_key_raw} is not in storage.",
                            cause=str(exc),
                            fix="Re-upload the mask and retry.",
                            retryable=False,
                        ),
                        status_code=500,
                    ) from exc
                # Mask is always PNG by upload contract; ensure_png_bytes
                # is still cheap and gives us a real decode for safety.
                try:
                    mask_bytes = ensure_png_bytes(mask_bytes)
                except ValueError as exc:
                    raise AgentErrorException(
                        AgentError(
                            code="VALIDATION_REFERENCE_IMAGE_UNDECODABLE",
                            message="遮罩檔案無法解碼",
                            problem="Mask bytes could not be decoded by PIL.",
                            cause=str(exc),
                            fix="Re-upload the mask.",
                            retryable=False,
                        ),
                        status_code=500,
                    ) from exc
                log_input_keys.append(str(mask_key_raw))

            # Step 3: AI dispatch.
            dispatch = _decide_dispatch(payload)
            ai_result: AIGenerationResult
            if dispatch == "edit_inpaint":
                assert mask_bytes is not None  # guarded by _decide_dispatch
                ai_result = await ai_client.edit_inpaint(
                    base_image_bytes=base_bytes,
                    mask_png_bytes=mask_bytes,
                    prompt=final_prompt,
                )
            else:
                ai_result = await ai_client.edit_image2image(
                    base_image_bytes=base_bytes,
                    reference_image_bytes=reference_image_bytes or None,
                    prompt=final_prompt,
                )

            if await _is_cancel_requested(session_factory, task_uuid):
                await _commit_cancelled(session_factory, task_uuid, redis)
                return {"task_id": task_id, "ok": False, "reason": "cancelled"}

            # Step 4: storage.
            storage.put(output_key, ai_result.image_bytes, _CONTENT_TYPE_PNG)
            output_orphaned = True

            thumb_bytes = make_thumbnail_png(ai_result.image_bytes)
            if thumb_bytes is not None:
                try:
                    storage.put(thumb_key, thumb_bytes, _CONTENT_TYPE_PNG)
                except StorageError:
                    _logger.warning(
                        "create_alias: thumbnail put failed for %s",
                        output_key,
                    )

            # Step 5: write generation_log + alias rows atomically.
            try:
                async with session_factory() as db:
                    log_row = await generation_log_repo.insert_success(
                        db,
                        user_id=user_id,
                        character_id=character_id,
                        entity_type="alias",
                        entity_id=alias_id,
                        model_name="gpt-image-2",
                        model_version=ai_result.model_version,
                        final_prompt=final_prompt,
                        input_image_keys=log_input_keys or None,
                        parameters={
                            "input_mode": payload.get("input_mode"),
                            "dispatch": dispatch,
                        },
                        cost_units=ai_result.cost_units,
                        duration_ms=ai_result.duration_ms,
                        started_at=started_at,
                        completed_at=datetime.now(UTC),
                    )
                    # Mask metadata persisted on the row so the frontend
                    # can show "this alias was an inpaint" without re-
                    # querying the masks table (which is character-
                    # scoped and lifecycle-bound).
                    mask_data: dict[str, Any] | None
                    if payload.get("mask_id"):
                        mask_data = {
                            "mask_id": str(payload["mask_id"]),
                            "mask_key": mask_key_raw,
                        }
                    else:
                        mask_data = None
                    try:
                        alias_row = await alias_repo.insert(
                            db,
                            alias_id=alias_id,
                            character_id=character_id,
                            name=str(payload["name"]),
                            prompt=final_prompt,
                            user_freeform_note=payload.get("freeform_note"),
                            input_mode=_model_input_mode_for(str(payload["input_mode"])),
                            mask_data=mask_data,
                            image_key=output_key,
                            generation_log_id=log_row.id,
                        )
                        await db.commit()
                        output_orphaned = False
                        alias_committed = True
                    except IntegrityError as exc:
                        await db.rollback()
                        err_text = str(exc.orig or exc)
                        # Idempotent retry: PK collision means a previous
                        # attempt committed; reuse the existing row.
                        if "aliases_pkey" in err_text:
                            existing = await alias_repo.get_active(db, alias_id)
                            if existing is not None:
                                alias_row = existing
                                output_orphaned = False
                                alias_committed = True
                                _logger.info(
                                    "create_alias: idempotent retry — alias %s "
                                    "already committed; reusing row",
                                    alias_id,
                                )
                            else:
                                raise
                        elif "uq_aliases_character_name" in err_text:
                            # Name collision — service-layer probe missed
                            # it, surface as 409 retryable=False.
                            raise AgentErrorException(
                                AgentError(
                                    code="CONFLICT_DUPLICATE_NAME",
                                    message="此造型名稱已存在",
                                    problem="Alias name collided on commit "
                                    "(another alias with the same name exists "
                                    "for this character).",
                                    cause="The pre-flight uniqueness probe "
                                    "missed a concurrent alias create.",
                                    fix="Pick a different name and retry.",
                                    retryable=False,
                                ),
                                status_code=409,
                            ) from exc
                        else:
                            raise
                    # No post-commit `db.refresh(alias_row)`: the row was
                    # already refreshed inside `alias_repo.insert` after
                    # the `db.flush()`, so all attributes (id, name,
                    # input_mode, image_key, created_at) are populated
                    # and survive the commit. Re-refreshing here on the
                    # same async session is a known hazard — see
                    # create_checkpoint.py round-13/14 for the lesson:
                    # an expired-attribute access after the `with` block
                    # would trip MissingGreenlet, and the DTO-build
                    # try/except is a safety net we'd rather not rely on
                    # on the happy path.
            finally:
                if output_orphaned:
                    for stale_key in (output_key, thumb_key):
                        try:
                            storage.delete(stale_key)
                        except StorageError:
                            _logger.warning(
                                "create_alias: orphan cleanup failed for %s",
                                stale_key,
                            )

        # Build the DTO post-commit. Same recovery pattern as
        # create_checkpoint — the row is durable, so a hiccup here
        # falls back to a minimal payload rather than failing the task.
        try:
            alias_dto = build_alias_dto(alias_row, storage)
        except Exception:  # noqa: BLE001 — DTO is recoverable post-commit
            _logger.warning(
                "create_alias: in-session DTO build failed; re-reading",
                exc_info=True,
            )
            try:
                async with session_factory() as db_read:
                    reread = await alias_repo.get_active(db_read, alias_id)
                if reread is not None:
                    alias_dto = build_alias_dto(reread, storage)
                else:
                    raise RuntimeError("alias vanished post-commit")
            except Exception:  # noqa: BLE001 — last-resort minimal payload
                _logger.exception("create_alias: DTO recovery failed; using minimal payload")
                alias_dto = None

        if alias_dto is not None:
            result_payload = {"alias": alias_dto.model_dump(mode="json")}
        else:
            result_payload = {
                "alias": {
                    "id": str(alias_id),
                    "character_id": str(character_id),
                }
            }
        async with session_factory() as db:
            await task_repo.mark_completed(
                db,
                task_uuid,
                entity_type="alias",
                entity_id=alias_id,
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
        if alias_committed:
            _logger.warning(
                "run_create_alias: post-commit AgentError for task %s; "
                "raising for arq retry. code=%s",
                task_id,
                exc.error.code,
            )
            raise
        _logger.warning(
            "run_create_alias: task %s failing with %s",
            task_id,
            exc.error.code,
        )
        await _commit_failed(session_factory, task_uuid, exc.error, redis)
        return {"task_id": task_id, "ok": False, "reason": exc.error.code}
    except Exception as exc:  # noqa: BLE001 — catch-all so the task row never sticks
        if alias_committed:
            _logger.warning(
                "run_create_alias: post-commit failure for task %s; raising for arq retry",
                task_id,
                exc_info=True,
            )
            raise
        _logger.exception("run_create_alias: unhandled exception for task %s", task_id)
        await _commit_failed(session_factory, task_uuid, _agent_error_from_exception(exc), redis)
        return {"task_id": task_id, "ok": False, "reason": "internal_error"}
