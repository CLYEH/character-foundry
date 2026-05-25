"""`alias.*` MCP tools (T-085) — Wave B's second packaged tool + CRUD wraps.

Five tools per `planning/agent-interface/endpoint-mcp-mapping.md` §2.3 / §3:

  • `alias.add`     packaged — add an alias end-to-end across all four input
                    modes (optional mask upload → create alias → poll the
                    generation task → return the finished alias)
  • `alias.list`    → GET    /v1/characters/{id}/aliases
  • `alias.get`     → GET    /v1/aliases/{id}
  • `alias.rename`  → PATCH  /v1/aliases/{id}
  • `alias.delete`  → DELETE /v1/aliases/{id}        (soft delete)

Invocation model mirrors `app/mcp/tools/character.py` (T-084): each tool
resolves the caller from the MCP auth contextvar (`require_mcp_scopes` →
`require_user_context`), opens short-lived `AsyncSession`s (tools run inside
the JSON-RPC dispatch loop, not a FastAPI request scope), and calls the SAME
`alias_service` layer the REST routes use. Service-layer
`AgentErrorException`s are translated to MCP `ToolError`s carrying the
identical AgentError envelope, and DTO assembly reuses `build_alias_dto` so
the MCP wire shape can't drift from `/v1/*`.

`alias.add` follows `character.create`'s packaged-tool pattern: progress
notifications per phase (`uploading_mask` / `generating_alias`), a
service-driven async task it polls to completion, and a phase-tagged error
envelope `{error, phase}` on failure. Unlike `character.create` there is no
half-built session to abandon — alias creation writes no row until the worker
succeeds, so a failed run leaves nothing behind (an uploaded mask is reusable
and character-scoped, reclaimed by the Sprint 5 cleanup job, same as the REST
upload-then-fail path).

Reference-image constraint (Q-D7): Phase 1 has no character-scoped reference
upload endpoint, so `image` / `mixed` modes consume existing
`reference_image_ids` from the Base's source creation session. Inline
`reference_images` bytes are rejected with guidance toward `reference_image_ids`.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import uuid
from io import BytesIO
from typing import Any

from mcp.server.fastmcp.exceptions import ToolError
from PIL import Image, UnidentifiedImageError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_storage
from app.auth.scopes import SCOPE_CHARACTER_READ, SCOPE_CHARACTER_WRITE, SCOPE_TASK_READ
from app.core.errors import (
    AgentError,
    AgentErrorException,
    auth_invalid_token,
    not_found_character,
    validation_reference_image_too_large,
    validation_reference_image_undecodable,
    validation_reference_image_unsupported_type,
)
from app.core.permissions import assert_can_modify_character
from app.core.redis_client import get_arq_pool
from app.db.session import async_session_factory
from app.mcp.auth import require_mcp_scopes, require_user_context, translate_agent_errors
from app.mcp.registry import MCPTool, register
from app.mcp.schemas.alias import (
    AliasAddInput,
    AliasAddResult,
    AliasDeleteInput,
    AliasDeleteResult,
    AliasGetInput,
    AliasListInput,
    AliasRenameInput,
)
from app.models.user import User
from app.repositories import character_repo, mask_repo, motion_repo
from app.schemas.alias import (
    AliasListResponse,
    AliasResponse,
    CreateAliasRequest,
)
from app.schemas.alias_builder import build_alias_dto
from app.schemas.character import NameStr
from app.schemas.prompt import AliasInputMode, MaskInput
from app.services import alias_service
from app.storage.backend import StorageBackend
from app.storage.errors import StorageError

_logger = logging.getLogger(__name__)

# Inpaint masks are PNG-only and share the reference-image size limit
# (mirrors `aliases.py::upload_alias_mask`, which the MCP path replaces with a
# base64 intake). Bound the ENCODED length before decoding so an oversized
# payload is rejected without materializing a huge blob in memory (the REST
# route streams the multipart body in chunks and aborts at the cap).
_MASK_SIZE_LIMIT_BYTES = 10 * 1024 * 1024
_MAX_MASK_B64_CHARS = _MASK_SIZE_LIMIT_BYTES * 4 // 3 + 4
# Decompression-bomb guard: a ≤10MB PNG can still decode to billions of
# pixels. Cap the decoded RGBA footprint; PIL reads dimensions from the header
# without decoding pixels, so we reject before forcing a full `.load()`.
_MAX_MASK_DECODED_BYTES = 200 * 1024 * 1024

# Phase labels for `alias.add`'s synchronous steps — the failure envelope's
# `phase`. `uploading_mask` covers mask decode + format + ownership + store (only
# when a `mask_file` is supplied); `generating_alias` covers the alias enqueue +
# its synchronous validation (`enqueue_alias` raises not-found / 403 / duplicate-
# name there). Now that the tool returns a handle instead of polling (T-087),
# the actual generation + any worker-side failure (incl. the VALIDATION_MASK_EMPTY
# content check that needs the base image) are observed by the agent via
# `task.get`, not surfaced here.
_PHASE_UPLOADING_MASK = "uploading_mask"
_PHASE_GENERATING = "generating_alias"


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


def _tool_error(error: AgentError) -> ToolError:
    """Wrap an AgentError as a ToolError with the standard `{error}` envelope.

    Used for tool-entry validation (before any phase begins), matching the
    shape `require_mcp_scopes` / `translate_agent_errors` produce so clients
    parse one envelope everywhere.
    """
    return ToolError(json.dumps({"error": error.model_dump(mode="json")}))


def _phase_tool_error(phase: str, error: AgentError) -> ToolError:
    """Packaged-tool error envelope: the standard AgentError plus a sibling
    `phase` so the agent knows which step failed (ticket §error handling)."""
    return ToolError(json.dumps({"error": error.model_dump(mode="json"), "phase": phase}))


def _agent_error_from_unexpected(exc: BaseException) -> AgentError:
    """Wrap a non-AgentError infra failure (StorageError, DB error, …) into the
    standard envelope so a packaged-tool phase still surfaces a structured error
    (mirrors the worker's `_agent_error_from_exception`)."""
    return AgentError(
        code="INTERNAL_UNEXPECTED_ERROR",
        message="系統發生未預期錯誤",
        problem=f"Unhandled {type(exc).__name__} in alias.add: {exc}",
        cause="Infra/runtime failure inside a packaged-tool phase (e.g. storage or DB).",
        fix="Retry; if persistent, inspect the api logs.",
        retryable=True,
    )


# ---------------------------------------------------------------------------
# CRUD 1:1 wraps
# ---------------------------------------------------------------------------


async def alias_list(character_id: uuid.UUID) -> AliasListResponse:
    """List a character's active aliases (`GET /v1/characters/{id}/aliases`)."""
    auth = require_mcp_scopes(SCOPE_CHARACTER_READ)
    user_id = require_user_context(auth)
    storage = get_storage()
    with translate_agent_errors():
        factory = async_session_factory()
        async with factory() as db:
            user = await _load_user(db, user_id)
            aliases = await alias_service.list_aliases_for_character(
                db, user=user, character_id=character_id
            )
            # Per-row motion count, same as the REST list route (Phase 1 alias
            # counts are tiny so the per-row query is cheap).
            items = []
            for alias in aliases:
                motion_count = await motion_repo.count_active_for_alias(db, alias_id=alias.id)
                items.append(build_alias_dto(alias, storage, motion_count=motion_count))
            return AliasListResponse(items=items)


async def alias_get(alias_id: uuid.UUID) -> AliasResponse:
    """Fetch one alias's detail (`GET /v1/aliases/{id}`)."""
    auth = require_mcp_scopes(SCOPE_CHARACTER_READ)
    user_id = require_user_context(auth)
    storage = get_storage()
    with translate_agent_errors():
        factory = async_session_factory()
        async with factory() as db:
            user = await _load_user(db, user_id)
            detail = await alias_service.get_alias_detail(db, user=user, alias_id=alias_id)
            return AliasResponse(
                alias=build_alias_dto(detail.alias, storage, motion_count=detail.motion_count)
            )


async def alias_rename(alias_id: uuid.UUID, name: NameStr) -> AliasResponse:
    """Rename an alias (`PATCH /v1/aliases/{id}`)."""
    auth = require_mcp_scopes(SCOPE_CHARACTER_WRITE)
    user_id = require_user_context(auth)
    storage = get_storage()
    with translate_agent_errors():
        factory = async_session_factory()
        async with factory() as db:
            user = await _load_user(db, user_id)
            alias = await alias_service.update_alias_name(
                db, user=user, alias_id=alias_id, new_name=name
            )
            # Re-count so the response DTO matches the GET /v1/aliases/{id}
            # shape exactly (rename doesn't touch motions; cheap scalar query).
            motion_count = await motion_repo.count_active_for_alias(db, alias_id=alias.id)
            return AliasResponse(alias=build_alias_dto(alias, storage, motion_count=motion_count))


async def alias_delete(alias_id: uuid.UUID) -> AliasDeleteResult:
    """Soft-delete an alias + cascade-soft-delete its motions (`DELETE /v1/aliases/{id}`)."""
    auth = require_mcp_scopes(SCOPE_CHARACTER_WRITE)
    user_id = require_user_context(auth)
    with translate_agent_errors():
        factory = async_session_factory()
        async with factory() as db:
            user = await _load_user(db, user_id)
            await alias_service.soft_delete_alias(db, user=user, alias_id=alias_id)
            return AliasDeleteResult(alias_id=alias_id)


# ---------------------------------------------------------------------------
# Packaged tool — alias.add
# ---------------------------------------------------------------------------


def _decode_mask_image(b64: str) -> bytes:
    """Decode a base64 mask PNG, tolerating an optional data-URL prefix.

    Bounds the encoded length BEFORE decoding so an oversized payload is
    rejected without first being materialized (the REST mask route's streaming
    read bounds the equivalent multipart body).
    """
    payload = b64.split(",", 1)[1] if b64.startswith("data:") and "," in b64 else b64
    if len(payload) > _MAX_MASK_B64_CHARS:
        raise validation_reference_image_too_large(
            size_bytes=len(payload) * 3 // 4, limit_bytes=_MASK_SIZE_LIMIT_BYTES
        )
    try:
        return base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise validation_reference_image_undecodable() from exc


def _validate_mask_image(raw: bytes) -> None:
    """Validate decoded mask bytes are a real, in-bounds PNG.

    Masks are PNG-only (the inpaint convention uses the alpha channel to mark
    the edit region — JPEG/WebP would drop it). base64 carries no content-type,
    so we read `im.format` instead of a multipart MIME header, guard against a
    decompression bomb before the full decode, then force the decode so a
    truncated file fails here — mirroring `aliases.py::upload_alias_mask`.
    """
    if len(raw) > _MASK_SIZE_LIMIT_BYTES:
        raise validation_reference_image_too_large(
            size_bytes=len(raw), limit_bytes=_MASK_SIZE_LIMIT_BYTES
        )
    try:
        with Image.open(BytesIO(raw)) as im:
            fmt = (im.format or "").upper()
            width, height = im.size
            if fmt != "PNG":
                raise validation_reference_image_unsupported_type()
            if width * height * 4 > _MAX_MASK_DECODED_BYTES:  # RGBA footprint
                raise validation_reference_image_too_large(
                    size_bytes=width * height * 4, limit_bytes=_MAX_MASK_DECODED_BYTES
                )
            im.load()  # full decode AFTER the cheap header checks — catches truncation
    except Image.DecompressionBombError as exc:
        raise validation_reference_image_too_large(
            size_bytes=_MAX_MASK_DECODED_BYTES + 1, limit_bytes=_MAX_MASK_DECODED_BYTES
        ) from exc
    except (OSError, ValueError, UnidentifiedImageError) as exc:
        raise validation_reference_image_undecodable() from exc


async def _upload_mask(
    storage: StorageBackend,
    factory: Any,
    *,
    user_id: uuid.UUID,
    character_id: uuid.UUID,
    mask_b64: str,
) -> uuid.UUID:
    """Decode → validate → permission-check → store → persist a mask; return id.

    Replicates the REST mask route's contract (`aliases.py::upload_alias_mask`):
    PNG-only validation, character-ownership gate, storage layout
    `creation-sessions/{character_id}/masks/{mask_id}.png`, and orphan cleanup
    if the row insert fails after the blob is written.
    """
    raw = _decode_mask_image(mask_b64)
    _validate_mask_image(raw)

    # Ownership gate in a short-lived session, BEFORE storing — mirrors the
    # REST route. Cross-team → NOT_FOUND_CHARACTER, same-team-non-owner → 403.
    async with factory() as db:
        user = await _load_user(db, user_id)
        character = await character_repo.get_active(db, character_id)
        if character is None:
            raise not_found_character()
        assert_can_modify_character(character, user)

    mask_id = uuid.uuid4()
    storage_key = f"creation-sessions/{character_id}/masks/{mask_id}.png"
    storage.put(storage_key, raw, "image/png")
    committed = True
    try:
        async with factory() as db:
            await mask_repo.insert(
                db,
                mask_id=mask_id,
                character_id=character_id,
                uploaded_by_user_id=user_id,
                storage_key=storage_key,
                mime_type="image/png",
                size_bytes=len(raw),
            )
            await db.commit()
        committed = False  # row now references the file
    except BaseException:
        if committed:
            try:
                storage.delete(storage_key)
            except StorageError:
                _logger.warning("alias.add: orphan mask cleanup failed for %s", storage_key)
        raise
    return mask_id


async def alias_add(
    character_id: uuid.UUID,
    name: NameStr,
    input_mode: AliasInputMode,
    freeform_note: str | None = None,
    reference_image_ids: list[uuid.UUID] | None = None,
    mask_file: str | None = None,
    mask_id: uuid.UUID | None = None,
    reference_images: list[str] | None = None,
) -> AliasAddResult:
    """Submit an alias-generation job and return a handle (non-blocking, T-087).

    Does the synchronous parts inline — optional mask upload + alias enqueue
    across all four input modes (`text` / `image` / `inpaint` / `mixed`) — then
    returns `{task_id, alias_id, status}` immediately. The agent polls
    `task.get(task_id)` until terminal (a generation failure, incl. the
    worker-side VALIDATION_MASK_EMPTY content check, surfaces there as
    `status="failed"` with a structured `error`) and fetches the finished alias
    via `alias.get(alias_id)` on `completed`. The generation work runs in the arq
    worker, independent of this MCP connection, so a dropped connection never
    loses it; the agent re-queries with the ids it already holds.

    `image` / `mixed` modes consume existing `reference_image_ids` from the
    character's Base source creation session — Phase 1 has no character-scoped
    reference upload endpoint (Q-D7), so inline `reference_images` bytes are
    rejected with guidance. `mask_file` (upload new) and `mask_id` (reuse) are
    mutually exclusive. Mask-upload failures are phase-tagged `uploading_mask`;
    synchronous enqueue-validation failures `generating_alias`.
    """
    auth = require_mcp_scopes(SCOPE_CHARACTER_WRITE, SCOPE_TASK_READ)
    user_id = require_user_context(auth)

    # ----- Tool-entry validation (pre-phase): plain (non-phase) tool errors.
    if reference_images:
        raise _tool_error(
            AgentError(
                code="VALIDATION_ALIAS_REFERENCE_UPLOAD_UNSUPPORTED",
                message="此造型模式不支援直接上傳新參考圖",
                problem=(
                    "alias.add received inline reference image bytes, but Phase 1 has no "
                    "character-scoped reference upload endpoint."
                ),
                cause="Reference images can only be uploaded during the Base creation session.",
                fix=(
                    "Pass `reference_image_ids` referencing images uploaded during the "
                    "character's Base creation session instead of inline bytes."
                ),
                retryable=False,
            )
        )
    if mask_file is not None and mask_id is not None:
        raise _tool_error(
            AgentError(
                code="VALIDATION_MASK_INPUT_CONFLICT",
                message="遮罩參數衝突",
                problem="alias.add received both `mask_file` and `mask_id`.",
                cause="`mask_file` (upload new) and `mask_id` (reuse existing) are mutually exclusive.",
                fix="Supply exactly one of `mask_file` or `mask_id`.",
                retryable=False,
            )
        )

    factory = async_session_factory()

    # ----- Phase 1 (optional): upload the mask, resolve to a mask_id.
    resolved_mask_id: uuid.UUID | None = mask_id
    if mask_file is not None:
        try:
            resolved_mask_id = await _upload_mask(
                get_storage(),
                factory,
                user_id=user_id,
                character_id=character_id,
                mask_b64=mask_file,
            )
        except AgentErrorException as exc:
            raise _phase_tool_error(_PHASE_UPLOADING_MASK, exc.error) from exc
        except Exception as exc:  # noqa: BLE001 — infra failure → structured tool error
            raise _phase_tool_error(
                _PHASE_UPLOADING_MASK, _agent_error_from_unexpected(exc)
            ) from exc

    # ----- Phase 2: enqueue the alias-generation task. `enqueue_alias` raises
    # synchronous AgentErrors (validation / not-found / duplicate-name) that
    # belong to this phase; any AgentError OR infra exception here is
    # phase-tagged `generating_alias`. The generation itself runs in the worker
    # and is observed by the agent via task.get.
    try:
        body = CreateAliasRequest(
            name=name,
            input_mode=input_mode,
            freeform_note=freeform_note,
            reference_image_ids=reference_image_ids,
            mask=MaskInput(mask_id=resolved_mask_id) if resolved_mask_id is not None else None,
        )
        arq_pool = await get_arq_pool()
        async with factory() as db:
            user = await _load_user(db, user_id)
            enqueued = await alias_service.enqueue_alias(
                db, arq_pool, user=user, character_id=character_id, body=body
            )
    except AgentErrorException as exc:
        raise _phase_tool_error(_PHASE_GENERATING, exc.error) from exc
    except Exception as exc:  # noqa: BLE001 — infra failure → structured tool error
        raise _phase_tool_error(_PHASE_GENERATING, _agent_error_from_unexpected(exc)) from exc

    return AliasAddResult(task_id=enqueued.task_id, alias_id=enqueued.alias_id)


# ---------------------------------------------------------------------------
# Registry entries
# ---------------------------------------------------------------------------

ALIAS_ADD = register(
    MCPTool(
        name="alias.add",
        description=(
            "Submit an alias-generation job for a character across all input modes "
            "(text / image / inpaint / mixed) and return a handle immediately "
            "(non-blocking): optionally upload an inpaint mask, then enqueue. Returns "
            "{task_id, alias_id}: poll task.get(task_id) until completed (a generation "
            "failure surfaces there as failed + structured error), then alias.get(alias_id). "
            "image/mixed modes use existing reference_image_ids from the Base's creation "
            "session (no inline upload). The job survives connection drops — re-query with task_id."
        ),
        # task:read stays required (and GET /v1/tasks stays bundled): the tool's
        # workflow is submit-then-poll, so a token that can submit must also be
        # able to track the task it created — fail fast at submit otherwise.
        scopes=[SCOPE_CHARACTER_WRITE, SCOPE_TASK_READ],
        bundles=[
            "POST /v1/characters/{character_id}/aliases/masks",
            "POST /v1/characters/{character_id}/aliases",
            "GET /v1/tasks/{task_id}",
        ],
        input_schema=AliasAddInput,
        output_schema=AliasAddResult,
        handler=alias_add,
    )
)

ALIAS_LIST = register(
    MCPTool(
        name="alias.list",
        description="List a character's active aliases.",
        scopes=[SCOPE_CHARACTER_READ],
        bundles=["GET /v1/characters/{character_id}/aliases"],
        input_schema=AliasListInput,
        output_schema=AliasListResponse,
        handler=alias_list,
    )
)

ALIAS_GET = register(
    MCPTool(
        name="alias.get",
        description="Fetch one alias's full detail.",
        scopes=[SCOPE_CHARACTER_READ],
        bundles=["GET /v1/aliases/{alias_id}"],
        input_schema=AliasGetInput,
        output_schema=AliasResponse,
        handler=alias_get,
    )
)

ALIAS_RENAME = register(
    MCPTool(
        name="alias.rename",
        description="Rename an alias.",
        scopes=[SCOPE_CHARACTER_WRITE],
        bundles=["PATCH /v1/aliases/{alias_id}"],
        input_schema=AliasRenameInput,
        output_schema=AliasResponse,
        handler=alias_rename,
    )
)

ALIAS_DELETE = register(
    MCPTool(
        name="alias.delete",
        description="Soft-delete an alias (cascade-soft-deletes its motions).",
        scopes=[SCOPE_CHARACTER_WRITE],
        bundles=["DELETE /v1/aliases/{alias_id}"],
        input_schema=AliasDeleteInput,
        output_schema=AliasDeleteResult,
        handler=alias_delete,
    )
)
