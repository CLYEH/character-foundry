"""`character.*` MCP tools (T-084) — Wave B's first packaged tool + CRUD wraps.

Ten tools per `planning/agent-interface/endpoint-mcp-mapping.md` §2.1 / §2.2 / §3:

  • `character.create`          packaged — bootstrap a Character end-to-end
                                (create → [upload refs] → checkpoint → select base)
  • `character.list`            → GET    /v1/characters
  • `character.get`             → GET    /v1/characters/{id}
  • `character.rename`          → PATCH  /v1/characters/{id}
  • `character.delete`          → DELETE /v1/characters/{id}        (soft delete)
  • `character.restore`         → POST   /v1/characters/{id}/restore
  • `character.fork`            → POST   /v1/checkpoints/{id}/fork
  • `character.get_session`     → GET    /v1/creation-sessions/{id}
  • `character.abandon_session` → POST   /v1/creation-sessions/{id}/abandon
  • `character.get_checkpoint`  → GET    /v1/checkpoints/{id}

Invocation model: same-process, mirroring `app/mcp/tools/task.py`. Each tool
resolves the caller from the MCP auth contextvar (`require_mcp_scopes` →
`require_user_context`), opens short-lived `AsyncSession`s (tools run inside
the JSON-RPC dispatch loop, not a FastAPI request scope), and calls the SAME
service layer the REST routes use. Service-layer `AgentErrorException`s are
translated to MCP `ToolError`s carrying the identical AgentError envelope.

DTO assembly reuses the builders the REST routes use (imported from
`app.api.routes.characters` + `app.schemas.checkpoint_builder`) so the MCP
wire shape can't drift from `/v1/*` — the agent-interface contract treats
human/agent surface drift as a bug, not a feature. `app.mcp → app.api` is
permitted by the import-linter contracts (only `app.api → app.models` and
`app.ai → app.api` are forbidden).

The packaged `character.create` is the reference pattern T-085 (`alias.add`)
and T-086 (`motion.generate`) copy: progress notifications per phase, a
service-driven async sub-task it polls to completion, and abandon-on-failure
cleanup so a failed run leaves no in-progress session behind.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import time
import uuid
from io import BytesIO
from typing import Annotated, Any

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError
from PIL import Image, UnidentifiedImageError
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_storage
from app.api.routes.characters import (
    _bases_for_characters,
    _character_to_detail_dto,
    _owners_by_ids,
    build_character_list_dto,
    build_character_list_dto_with_owners,
)
from app.auth.scopes import SCOPE_CHARACTER_READ, SCOPE_CHARACTER_WRITE, SCOPE_TASK_READ
from app.core.errors import (
    AgentError,
    AgentErrorException,
    auth_invalid_token,
    not_found_task,
    validation_reference_image_required,
    validation_reference_image_too_large,
    validation_reference_image_undecodable,
    validation_reference_image_unsupported_type,
)
from app.core.redis_client import get_arq_pool, get_redis
from app.db.session import async_session_factory
from app.mcp.auth import require_mcp_scopes, require_user_context, translate_agent_errors
from app.mcp.progress import report_progress
from app.mcp.registry import MCPTool, register
from app.mcp.schemas.character import (
    CharacterAbandonSessionInput,
    CharacterCreateInput,
    CharacterCreateResult,
    CharacterDeleteInput,
    CharacterDeleteResult,
    CharacterForkInput,
    CharacterGetCheckpointInput,
    CharacterGetInput,
    CharacterGetSessionInput,
    CharacterListInput,
    CharacterRenameInput,
    CharacterRestoreInput,
    SessionAbandonResult,
)
from app.models.user import User
from app.repositories import task_repo
from app.schemas.base import ForkCheckpointResponse
from app.schemas.character import (
    CharacterDetailResponse,
    CharacterListResponse,
    CharacterResponse,
    InputMode,
    NameStr,
)
from app.schemas.checkpoint import (
    CheckpointAspectRatio,
    CheckpointResponse,
    CreationSessionDetailResponse,
)
from app.schemas.checkpoint_builder import build_base_dto, build_checkpoint_dto
from app.schemas.creation_session import CreationSessionDTO
from app.services import (
    base_service,
    character_service,
    checkpoint_service,
    creation_session_service,
    fork_service,
)
from app.storage.backend import StorageBackend
from app.storage.errors import StorageError

_logger = logging.getLogger(__name__)

# Reference-image upload limit — mirrors the REST route
# (`api/app/api/routes/reference_images.py`). 10MB per image.
_REFERENCE_SIZE_LIMIT_BYTES = 10 * 1024 * 1024
# Reject oversized base64 BEFORE decoding. The REST route streams the multipart
# body in 256KB chunks and aborts at the cap; the MCP path receives the whole
# string at once, so we bound the ENCODED length to avoid materializing a huge
# decoded blob in memory (base64 inflates ~4/3, plus padding).
_MAX_REFERENCE_B64_CHARS = _REFERENCE_SIZE_LIMIT_BYTES * 4 // 3 + 4
# Decompression-bomb guard: a ≤10MB file can still decode to billions of
# pixels. Cap the decoded RGBA footprint (~50MP). PIL reads dimensions from the
# header without decoding pixels, so we reject before forcing a full `.load()`.
# (The shared `ensure_png_bytes` has no such guard — that's a pre-existing REST
# exposure tracked in STATUS backlog S3.5-5.)
_MAX_REFERENCE_DECODED_BYTES = 200 * 1024 * 1024
# Match the REST route's MIME allowlist. base64 carries no content-type, so we
# read the format from the decoded image instead of a header, and store the
# ORIGINAL bytes under the detected format (mirroring the REST route, which
# stores the uploaded payload as-is — NOT re-encoded — so the ≤10MB cap on the
# input is also the cap on the stored blob; Codex PR #111 P1).
_FORMAT_TO_MIME_EXT: dict[str, tuple[str, str]] = {
    "PNG": ("image/png", "png"),
    "JPEG": ("image/jpeg", "jpg"),
    "WEBP": ("image/webp", "webp"),
}
_ALLOWED_IMAGE_FORMATS = frozenset(_FORMAT_TO_MIME_EXT)
# Cap references per call — the REST route is one-image-per-request; this
# packaged tool takes a list, so bound it to keep N×10MB memory in check.
_MAX_REFERENCE_IMAGES = 8

# character.create polls the checkpoint task to completion. Interval keeps the
# loop responsive without hammering Postgres; the timeout stays under the nginx
# `/mcp` `proxy_read_timeout` (T-082, ≥180s) so the tool gives up cleanly
# rather than having the connection cut from under it.
_POLL_INTERVAL_S = 1.0
_POLL_TIMEOUT_S = 170.0


async def _load_user(db: AsyncSession, user_id: uuid.UUID) -> User:
    """Load the calling user's row, or raise like /v1/*'s get_current_user.

    The user row can vanish between token resolution and this read (race with
    deletion). Mirror the REST surface: surface AUTH_INVALID_TOKEN rather than
    leaking account-existence state or letting None flow into a service call.
    """
    user = await db.get(User, user_id)
    if user is None:
        raise auth_invalid_token()
    return user


# ---------------------------------------------------------------------------
# CRUD 1:1 wraps
# ---------------------------------------------------------------------------


async def character_list(
    owner_id: str | None = None,
    q: str | None = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
    cursor: str | None = None,
) -> CharacterListResponse:
    """List characters visible to the caller (`GET /v1/characters`).

    `owner_id` accepts `me`, an explicit user UUID, or omitted (whole team) —
    same as the REST route. A malformed UUID degrades to an empty page rather
    than erroring, matching the route's graceful fallback.
    """
    auth = require_mcp_scopes(SCOPE_CHARACTER_READ)
    user_id = require_user_context(auth)
    storage = get_storage()
    with translate_agent_errors():
        factory = async_session_factory()
        async with factory() as db:
            user = await _load_user(db, user_id)
            if owner_id is None or owner_id == "":
                resolved_owner_id: uuid.UUID | None = None
            elif owner_id == "me":
                resolved_owner_id = user.id
            else:
                try:
                    resolved_owner_id = uuid.UUID(owner_id)
                except ValueError:
                    return CharacterListResponse(items=[], next_cursor=None)
            result = await character_service.list_characters(
                db,
                user=user,
                owner_id=resolved_owner_id,
                q=q,
                limit=limit,
                cursor_str=cursor,
            )
            # Same batched owner + base lookups the REST list path uses so the
            # MCP surface has identical behaviour (no N+1) — Codex round-7 P2
            # on the REST side.
            owners = await _owners_by_ids(db, {c.owner_id for c in result.items})
            base_keys = await _bases_for_characters(db, list(result.items))
            items = [
                build_character_list_dto_with_owners(
                    c, owners, base_image_key=base_keys.get(c.id), storage=storage
                )
                for c in result.items
            ]
            return CharacterListResponse(items=items, next_cursor=result.next_cursor)


async def character_get(character_id: uuid.UUID) -> CharacterDetailResponse:
    """Fetch one character's full detail (`GET /v1/characters/{id}`)."""
    auth = require_mcp_scopes(SCOPE_CHARACTER_READ)
    user_id = require_user_context(auth)
    storage = get_storage()
    with translate_agent_errors():
        factory = async_session_factory()
        async with factory() as db:
            user = await _load_user(db, user_id)
            character = await character_service.get_character_for_read(
                db, user=user, character_id=character_id
            )
            return CharacterDetailResponse(
                character=await _character_to_detail_dto(db, character, storage=storage)
            )


async def character_rename(
    character_id: uuid.UUID,
    name: NameStr,
) -> CharacterResponse:
    """Rename a character (`PATCH /v1/characters/{id}`)."""
    auth = require_mcp_scopes(SCOPE_CHARACTER_WRITE)
    user_id = require_user_context(auth)
    storage = get_storage()
    with translate_agent_errors():
        factory = async_session_factory()
        async with factory() as db:
            user = await _load_user(db, user_id)
            character = await character_service.update_character_name(
                db, user=user, character_id=character_id, new_name=name
            )
            return CharacterResponse(
                character=await build_character_list_dto(db, character, storage=storage)
            )


async def character_delete(character_id: uuid.UUID) -> CharacterDeleteResult:
    """Soft-delete a character (`DELETE /v1/characters/{id}`)."""
    auth = require_mcp_scopes(SCOPE_CHARACTER_WRITE)
    user_id = require_user_context(auth)
    with translate_agent_errors():
        factory = async_session_factory()
        async with factory() as db:
            user = await _load_user(db, user_id)
            await character_service.soft_delete_character(db, user=user, character_id=character_id)
            return CharacterDeleteResult(character_id=character_id)


async def character_restore(character_id: uuid.UUID) -> CharacterResponse:
    """Restore a soft-deleted character (`POST /v1/characters/{id}/restore`)."""
    auth = require_mcp_scopes(SCOPE_CHARACTER_WRITE)
    user_id = require_user_context(auth)
    storage = get_storage()
    with translate_agent_errors():
        factory = async_session_factory()
        async with factory() as db:
            user = await _load_user(db, user_id)
            character = await character_service.restore_character(
                db, user=user, character_id=character_id
            )
            return CharacterResponse(
                character=await build_character_list_dto(db, character, storage=storage)
            )


async def character_fork(
    checkpoint_id: uuid.UUID,
    new_character_name: NameStr,
) -> ForkCheckpointResponse:
    """Open a new character + session from a checkpoint (`POST /v1/checkpoints/{id}/fork`)."""
    auth = require_mcp_scopes(SCOPE_CHARACTER_WRITE)
    user_id = require_user_context(auth)
    storage = get_storage()
    with translate_agent_errors():
        factory = async_session_factory()
        async with factory() as db:
            user = await _load_user(db, user_id)
            forked = await fork_service.fork_from_checkpoint(
                db,
                storage,
                user=user,
                checkpoint_id=checkpoint_id,
                new_character_name=new_character_name,
            )
            character_dto = await build_character_list_dto(db, forked.character, storage=storage)
            session_dto = CreationSessionDTO(
                id=forked.creation_session.id,
                character_id=forked.creation_session.character_id,
                input_mode=forked.creation_session.input_mode,  # type: ignore[arg-type]
                status=forked.creation_session.status,  # type: ignore[arg-type]
                # Forked session always starts with one checkpoint (the copy
                # inserted at sequence=1) — matches the REST route.
                checkpoint_count=1,
                created_at=forked.creation_session.created_at,
                completed_at=forked.creation_session.completed_at,
            )
            return ForkCheckpointResponse(character=character_dto, creation_session=session_dto)


async def character_get_session(session_id: uuid.UUID) -> CreationSessionDetailResponse:
    """Inspect a creation session + its checkpoints (`GET /v1/creation-sessions/{id}`)."""
    auth = require_mcp_scopes(SCOPE_CHARACTER_READ)
    user_id = require_user_context(auth)
    storage = get_storage()
    with translate_agent_errors():
        factory = async_session_factory()
        async with factory() as db:
            user = await _load_user(db, user_id)
            result = await creation_session_service.get_session_for_read(
                db, user=user, session_id=session_id
            )
            # Checkpoint images are initiator-only (storage-layout §5.1): a
            # same-team non-initiator sees the session shell but no checkpoint
            # artefacts / count. Mirrors the REST route's gating.
            is_initiator = result.session.initiator_id == user.id
            visible_checkpoints = result.checkpoints if is_initiator else ()
            session_dto = CreationSessionDTO(
                id=result.session.id,
                character_id=result.session.character_id,
                input_mode=result.session.input_mode,  # type: ignore[arg-type]
                status=result.session.status,  # type: ignore[arg-type]
                checkpoint_count=len(visible_checkpoints),
                created_at=result.session.created_at,
                completed_at=result.session.completed_at,
            )
            checkpoint_dtos = [build_checkpoint_dto(c, storage) for c in visible_checkpoints]
            return CreationSessionDetailResponse(session=session_dto, checkpoints=checkpoint_dtos)


async def character_abandon_session(session_id: uuid.UUID) -> SessionAbandonResult:
    """Abandon an in-progress creation session (`POST /v1/creation-sessions/{id}/abandon`)."""
    auth = require_mcp_scopes(SCOPE_CHARACTER_WRITE)
    user_id = require_user_context(auth)
    with translate_agent_errors():
        factory = async_session_factory()
        async with factory() as db:
            user = await _load_user(db, user_id)
            session = await base_service.abandon_session(db, user=user, session_id=session_id)
            return SessionAbandonResult(session_id=session.id, status=session.status)


async def character_get_checkpoint(checkpoint_id: uuid.UUID) -> CheckpointResponse:
    """Fetch one checkpoint by id (`GET /v1/checkpoints/{id}`)."""
    auth = require_mcp_scopes(SCOPE_CHARACTER_READ)
    user_id = require_user_context(auth)
    storage = get_storage()
    with translate_agent_errors():
        factory = async_session_factory()
        async with factory() as db:
            user = await _load_user(db, user_id)
            checkpoint = await checkpoint_service.get_checkpoint_for_read(
                db, user=user, checkpoint_id=checkpoint_id
            )
            return CheckpointResponse(checkpoint=build_checkpoint_dto(checkpoint, storage))


# ---------------------------------------------------------------------------
# Packaged tool — character.create
# ---------------------------------------------------------------------------

# Phase labels shared with the other packaged tools (T-085 / T-086 extend with
# their own where needed). Sent as the progress-notification `message` so an
# agent can narrate which step is running.
_PHASE_CREATING = "creating_session"
_PHASE_UPLOADING = "uploading_references"
_PHASE_RUNNING = "running_checkpoint"
_PHASE_SELECTING = "selecting_base"


def _phase_tool_error(phase: str, error: AgentError) -> ToolError:
    """Build the packaged-tool error envelope: the standard AgentError plus a
    sibling `phase` so the agent knows which step failed (ticket §error handling).
    """
    return ToolError(json.dumps({"error": error.model_dump(mode="json"), "phase": phase}))


def _decode_reference_image(b64: str) -> bytes:
    """Decode a base64 reference image, tolerating an optional data-URL prefix.

    Bounds the encoded length BEFORE decoding so an oversized payload is
    rejected without first being materialized in memory (the REST route's
    streaming read bounds the equivalent multipart body).
    """
    payload = b64.split(",", 1)[1] if b64.startswith("data:") and "," in b64 else b64
    if len(payload) > _MAX_REFERENCE_B64_CHARS:
        raise validation_reference_image_too_large(
            size_bytes=len(payload) * 3 // 4, limit_bytes=_REFERENCE_SIZE_LIMIT_BYTES
        )
    try:
        return base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise validation_reference_image_undecodable() from exc


def _validate_reference_image(raw: bytes) -> tuple[str, str]:
    """Validate decoded reference bytes and return their `(mime, extension)`.

    Three checks the base64 path can't get for free the way the REST route does:
      • Format allowlist (PNG / JPEG / WebP) — base64 carries no content-type,
        so we read `im.format` instead of a multipart MIME header.
      • Decompression-bomb guard — `Image.open` reads dimensions from the
        header WITHOUT decoding pixels, so we reject an oversized image before
        forcing a full decode.
      • Decodability — `im.load()` (after the cheap header checks) forces a full
        pixel decode so a truncated/corrupt file fails here, exactly as the REST
        route's `ensure_png_bytes` validation does.

    Returns the detected `(mime, extension)` so the caller stores the ORIGINAL
    bytes under the right key — matching the REST route, which never re-encodes.
    """
    try:
        with Image.open(BytesIO(raw)) as im:
            fmt = (im.format or "").upper()
            width, height = im.size
            if fmt not in _ALLOWED_IMAGE_FORMATS:
                raise validation_reference_image_unsupported_type()
            if width * height * 4 > _MAX_REFERENCE_DECODED_BYTES:  # RGBA footprint
                raise validation_reference_image_too_large(
                    size_bytes=width * height * 4, limit_bytes=_MAX_REFERENCE_DECODED_BYTES
                )
            im.load()  # full decode AFTER the cheap header checks — catches truncation
    except Image.DecompressionBombError as exc:
        raise validation_reference_image_too_large(
            size_bytes=_MAX_REFERENCE_DECODED_BYTES + 1,
            limit_bytes=_MAX_REFERENCE_DECODED_BYTES,
        ) from exc
    except (OSError, ValueError, UnidentifiedImageError) as exc:
        raise validation_reference_image_undecodable() from exc
    return _FORMAT_TO_MIME_EXT[fmt]


async def _upload_reference_images(
    storage: StorageBackend,
    factory: Any,
    *,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    images_b64: list[str],
) -> list[uuid.UUID]:
    """Decode → validate → store → persist each reference image; return its ids.

    Replicates the REST reference-image route's contract: validate (size cap,
    format allowlist, full PIL decode) + a decompression-bomb guard, then store
    the ORIGINAL bytes under the detected format (NO re-encode), so the ≤10MB cap
    on the input is also the cap on the stored blob (Codex PR #111 P1 — a
    re-encode to PNG could balloon a compressed JPEG/WebP past the limit). The
    worker normalizes to PNG at generation time anyway.

    On a mid-loop failure, already-persisted reference rows/blobs from earlier
    iterations are NOT individually rolled back here — the caller abandons the
    session, and the session's lifecycle cleanup reclaims its reference rows +
    blobs (same disposition as a human-abandoned session). Only the in-flight
    image's blob is cleaned up inline (below).
    """
    if not images_b64:
        # Reference mode with no images: fail at this phase with the same
        # error the worker would raise later, but earlier + clearer.
        raise validation_reference_image_required()
    reference_ids: list[uuid.UUID] = []
    for b64 in images_b64:
        raw = _decode_reference_image(b64)
        if len(raw) > _REFERENCE_SIZE_LIMIT_BYTES:
            raise validation_reference_image_too_large(
                size_bytes=len(raw), limit_bytes=_REFERENCE_SIZE_LIMIT_BYTES
            )
        mime_type, extension = _validate_reference_image(raw)

        reference_id = uuid.uuid4()
        storage_key = f"checkpoints/{session_id}/references/{reference_id}.{extension}"
        storage.put(storage_key, raw, mime_type)
        committed = True
        try:
            signed_url = storage.get_signed_url(storage_key, expires_in_seconds=3600)
            async with factory() as db:
                user = await _load_user(db, user_id)
                created = await checkpoint_service.upload_reference_image(
                    db,
                    user=user,
                    session_id=session_id,
                    reference_id=reference_id,
                    storage_key=storage_key,
                    mime_type=mime_type,
                    size_bytes=len(raw),
                    signed_url=signed_url,
                )
            committed = False  # row now references the file
            reference_ids.append(created.reference.id)
        except BaseException:
            if committed:
                try:
                    storage.delete(storage_key)
                except StorageError:
                    _logger.warning(
                        "character.create: orphan reference cleanup failed for %s", storage_key
                    )
            raise
    return reference_ids


def _agent_error_from_task(task: Any) -> AgentError:
    """Reconstruct the AgentError a failed checkpoint task stored in `task.error`."""
    err = task.error
    if isinstance(err, dict):
        try:
            return AgentError(**err)
        except (TypeError, ValueError):
            pass
    return AgentError(
        code="INTERNAL_UNEXPECTED_ERROR",
        message="系統發生未預期錯誤",
        problem=f"Checkpoint task {task.id} failed without a structured error payload.",
        cause="Worker recorded a non-AgentError failure.",
        fix="Retry; if persistent, inspect the worker log.",
        retryable=True,
    )


def _agent_error_from_unexpected(exc: BaseException) -> AgentError:
    """Wrap a non-AgentError infra failure (StorageError, DB error, …) into the
    standard envelope so a packaged-tool phase still surfaces a structured error
    (mirrors the worker's `_agent_error_from_exception`)."""
    return AgentError(
        code="INTERNAL_UNEXPECTED_ERROR",
        message="系統發生未預期錯誤",
        problem=f"Unhandled {type(exc).__name__} in character.create: {exc}",
        cause="Infra/runtime failure inside a packaged-tool phase (e.g. storage or DB).",
        fix="Retry; if persistent, inspect the api logs.",
        retryable=True,
    )


async def _wait_for_checkpoint_task(
    factory: Any,
    ctx: Context[Any, Any, Any] | None,
    *,
    user_id: uuid.UUID,
    task_id: uuid.UUID,
) -> None:
    """Poll the checkpoint task to a terminal state, emitting running progress.

    Returns on `completed`. Raises `AgentErrorException` on `failed` (with the
    worker's recorded error), on `cancelled`, on a missing task row, or on
    timeout. Each poll opens a short-lived session so it observes the worker's
    committed writes (the worker runs in a separate process / event loop).
    """
    deadline = time.monotonic() + _POLL_TIMEOUT_S
    while True:
        async with factory() as db:
            task = await task_repo.get_owned(db, task_id=task_id, user_id=user_id)
        if task is None:
            raise not_found_task()
        prog = float(task.progress) if isinstance(task.progress, int | float) else 0.0
        await report_progress(
            ctx, progress=max(0.0, min(1.0, prog)), total=1.0, message=_PHASE_RUNNING
        )
        if task.status == "completed":
            return
        if task.status == "failed":
            raise AgentErrorException(_agent_error_from_task(task))
        if task.status == "cancelled":
            raise AgentErrorException(
                AgentError(
                    code="TASK_CANCELLED",
                    message="任務已取消",
                    problem=f"Checkpoint task {task_id} was cancelled before completion.",
                    cause="A concurrent task.cancel ran during character.create.",
                    fix="Retry character.create.",
                    retryable=True,
                )
            )
        if time.monotonic() >= deadline:
            raise AgentErrorException(
                AgentError(
                    code="MCP_TOOL_TIMEOUT",
                    message="生成逾時",
                    problem=(
                        f"character.create polled task {task_id} for "
                        f"{int(_POLL_TIMEOUT_S)}s without completion."
                    ),
                    cause="The generation task is taking longer than the tool's wait budget.",
                    fix=f"The task may still finish — poll task.get with task_id={task_id}, or retry.",
                    retryable=True,
                )
            )
        await asyncio.sleep(_POLL_INTERVAL_S)


async def _abandon_session_quietly(
    factory: Any, *, user_id: uuid.UUID, session_id: uuid.UUID
) -> None:
    """Best-effort abandon on failure so a failed run leaves no in-progress session.

    Swallows errors (e.g. the session is already terminal) so cleanup never
    masks the original failure the caller is about to surface.
    """
    try:
        async with factory() as db:
            user = await _load_user(db, user_id)
            await base_service.abandon_session(db, user=user, session_id=session_id)
    except Exception:  # noqa: BLE001 — cleanup is best-effort; original error wins
        _logger.warning(
            "character.create: abandon-on-failure cleanup failed for session %s",
            session_id,
            exc_info=True,
        )


async def character_create(
    name: NameStr,
    input_mode: InputMode,
    menu_selections: dict[str, Any] | None = None,
    freeform_note: str | None = None,
    reference_images: Annotated[list[str] | None, Field(max_length=_MAX_REFERENCE_IMAGES)] = None,
    aspect_ratio: CheckpointAspectRatio = "2:3",
    checkpoint_count: Annotated[int, Field(ge=1, le=10)] = 1,
    ctx: Context[Any, Any, Any] | None = None,
) -> CharacterCreateResult:
    """Bootstrap a Character end-to-end and return it with its locked Base.

    Bundles the four-step REST flow (create character + session → optionally
    upload references → run checkpoint generation → select base) plus internal
    task polling into one call. Emits a `notifications/progress` per phase
    (`creating_session` / `uploading_references` / `running_checkpoint` /
    `selecting_base`). On any sub-step failure the half-built session is
    abandoned and a phase-tagged AgentError is raised.

    `checkpoint_count` > 1 generates that many checkpoints and locks the last
    one — agents usually want the default of 1.
    """
    auth = require_mcp_scopes(SCOPE_CHARACTER_WRITE, SCOPE_TASK_READ)
    user_id = require_user_context(auth)

    # Phase 1: acquire infra deps + create character + session. The dep
    # accessors (get_redis / get_arq_pool) are awaited INSIDE this try so a
    # Redis/arq outage — a likely transient/startup failure — surfaces as the
    # documented `creating_session` phase-tagged error rather than a raw
    # exception (Codex PR #111 P2). No cleanup possible here (nothing committed
    # yet — create_character commits atomically), so it sits outside the
    # abandon-wrapped block.
    await report_progress(ctx, progress=0.05, total=1.0, message=_PHASE_CREATING)
    try:
        redis = await get_redis()
        arq_pool = await get_arq_pool()
        storage = get_storage()
        factory = async_session_factory()
        async with factory() as db:
            user = await _load_user(db, user_id)
            created = await character_service.create_character(
                db, redis, user=user, name=name, input_mode=input_mode
            )
            session_id = created.creation_session.id
    except AgentErrorException as exc:
        raise _phase_tool_error(_PHASE_CREATING, exc.error) from exc
    except Exception as exc:  # noqa: BLE001 — infra failure → structured tool error
        raise _phase_tool_error(_PHASE_CREATING, _agent_error_from_unexpected(exc)) from exc

    # Phases 2-4. `current_phase` tracks where we are so that ANY failure —
    # AgentError OR an infra exception (StorageError from storage.put /
    # get_signed_url, a DB/SQLAlchemy error, etc.) — abandons the half-built
    # session AND surfaces a phase-tagged error. Catching only AgentError here
    # would leave the session stuck `in_progress` on an infra failure and return
    # an unstructured tool error (Codex PR #111 P1).
    current_phase = _PHASE_UPLOADING
    try:
        reference_image_ids: list[uuid.UUID] = []
        if input_mode == "reference":
            await report_progress(ctx, progress=0.1, total=1.0, message=_PHASE_UPLOADING)
            reference_image_ids = await _upload_reference_images(
                storage,
                factory,
                user_id=user_id,
                session_id=session_id,
                images_b64=reference_images or [],
            )

        current_phase = _PHASE_RUNNING
        checkpoint_id: uuid.UUID | None = None
        for _ in range(checkpoint_count):
            async with factory() as db:
                user = await _load_user(db, user_id)
                enqueued = await checkpoint_service.enqueue_checkpoint(
                    db,
                    redis,
                    arq_pool,
                    user=user,
                    session_id=session_id,
                    mode="fresh",
                    base_checkpoint_id=None,
                    menu_selections=menu_selections,
                    freeform_note=freeform_note,
                    reference_image_ids=reference_image_ids or None,
                    aspect_ratio=aspect_ratio,
                )
            checkpoint_id = enqueued.checkpoint_id
            await _wait_for_checkpoint_task(factory, ctx, user_id=user_id, task_id=enqueued.task_id)

        # checkpoint_count >= 1 (schema-bounded), so the loop ran ≥ once.
        assert checkpoint_id is not None

        current_phase = _PHASE_SELECTING
        await report_progress(ctx, progress=0.95, total=1.0, message=_PHASE_SELECTING)
        async with factory() as db:
            user = await _load_user(db, user_id)
            selected = await base_service.select_base(
                db, user=user, session_id=session_id, checkpoint_id=checkpoint_id
            )
            character_detail = await _character_to_detail_dto(
                db, selected.character, storage=storage
            )
            base_dto = build_base_dto(selected.base, storage)
    except AgentErrorException as exc:
        await _abandon_session_quietly(factory, user_id=user_id, session_id=session_id)
        raise _phase_tool_error(current_phase, exc.error) from exc
    except Exception as exc:  # noqa: BLE001 — infra failure → abandon + structured error
        await _abandon_session_quietly(factory, user_id=user_id, session_id=session_id)
        raise _phase_tool_error(current_phase, _agent_error_from_unexpected(exc)) from exc

    await report_progress(ctx, progress=1.0, total=1.0, message="done")
    return CharacterCreateResult(character=character_detail, base=base_dto)


# ---------------------------------------------------------------------------
# Registry entries
# ---------------------------------------------------------------------------

CHARACTER_CREATE = register(
    MCPTool(
        name="character.create",
        description=(
            "Create a character end-to-end: bootstrap the creation session, "
            "optionally upload reference images, run checkpoint generation, and "
            "lock the result as the immutable Base. Blocks until done, emitting "
            "progress per phase. Returns the character detail + its Base."
        ),
        scopes=[SCOPE_CHARACTER_WRITE, SCOPE_TASK_READ],
        bundles=[
            "POST /v1/characters",
            "POST /v1/creation-sessions/{session_id}/reference-images",
            "POST /v1/creation-sessions/{session_id}/checkpoints",
            "GET /v1/tasks/{task_id}",
            "POST /v1/creation-sessions/{session_id}/select-base",
        ],
        input_schema=CharacterCreateInput,
        output_schema=CharacterCreateResult,
        handler=character_create,
    )
)

CHARACTER_LIST = register(
    MCPTool(
        name="character.list",
        description="List characters visible to the caller (filter by owner / name substring).",
        scopes=[SCOPE_CHARACTER_READ],
        bundles=["GET /v1/characters"],
        input_schema=CharacterListInput,
        output_schema=CharacterListResponse,
        handler=character_list,
    )
)

CHARACTER_GET = register(
    MCPTool(
        name="character.get",
        description="Fetch one character's full detail (base + aliases + session ref).",
        scopes=[SCOPE_CHARACTER_READ],
        bundles=["GET /v1/characters/{character_id}"],
        input_schema=CharacterGetInput,
        output_schema=CharacterDetailResponse,
        handler=character_get,
    )
)

CHARACTER_RENAME = register(
    MCPTool(
        name="character.rename",
        description="Rename a character.",
        scopes=[SCOPE_CHARACTER_WRITE],
        bundles=["PATCH /v1/characters/{character_id}"],
        input_schema=CharacterRenameInput,
        output_schema=CharacterResponse,
        handler=character_rename,
    )
)

CHARACTER_DELETE = register(
    MCPTool(
        name="character.delete",
        description="Soft-delete a character (recoverable via character.restore).",
        scopes=[SCOPE_CHARACTER_WRITE],
        bundles=["DELETE /v1/characters/{character_id}"],
        input_schema=CharacterDeleteInput,
        output_schema=CharacterDeleteResult,
        handler=character_delete,
    )
)

CHARACTER_RESTORE = register(
    MCPTool(
        name="character.restore",
        description="Restore a soft-deleted character.",
        scopes=[SCOPE_CHARACTER_WRITE],
        bundles=["POST /v1/characters/{character_id}/restore"],
        input_schema=CharacterRestoreInput,
        output_schema=CharacterResponse,
        handler=character_restore,
    )
)

CHARACTER_FORK = register(
    MCPTool(
        name="character.fork",
        description="Open a new character + creation session seeded from an existing checkpoint.",
        scopes=[SCOPE_CHARACTER_WRITE],
        bundles=["POST /v1/checkpoints/{checkpoint_id}/fork"],
        input_schema=CharacterForkInput,
        output_schema=ForkCheckpointResponse,
        handler=character_fork,
    )
)

CHARACTER_GET_SESSION = register(
    MCPTool(
        name="character.get_session",
        description="Inspect an in-progress creation session and its checkpoints (resume / debug).",
        scopes=[SCOPE_CHARACTER_READ],
        bundles=["GET /v1/creation-sessions/{session_id}"],
        input_schema=CharacterGetSessionInput,
        output_schema=CreationSessionDetailResponse,
        handler=character_get_session,
    )
)

CHARACTER_ABANDON_SESSION = register(
    MCPTool(
        name="character.abandon_session",
        description="Abandon an in-progress creation session (cannot abandon once a Base is locked).",
        scopes=[SCOPE_CHARACTER_WRITE],
        bundles=["POST /v1/creation-sessions/{session_id}/abandon"],
        input_schema=CharacterAbandonSessionInput,
        output_schema=SessionAbandonResult,
        handler=character_abandon_session,
    )
)

CHARACTER_GET_CHECKPOINT = register(
    MCPTool(
        name="character.get_checkpoint",
        description="Fetch one checkpoint by id (initiator-only; used by fork / resume flows).",
        scopes=[SCOPE_CHARACTER_READ],
        bundles=["GET /v1/checkpoints/{checkpoint_id}"],
        input_schema=CharacterGetCheckpointInput,
        output_schema=CheckpointResponse,
        handler=character_get_checkpoint,
    )
)
