"""AgentError envelope for structured API error responses.

The API returns errors in the shape `{"error": {<AgentError fields>}}` so
both UI and agent callers get a stable, machine-readable surface. See
planning/backend/api-shape.md §4 for field semantics and category prefixes.
"""

from __future__ import annotations

from contextvars import ContextVar

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

_REQUEST_ID_CTX: ContextVar[str | None] = ContextVar("request_id", default=None)


def current_request_id() -> str | None:
    return _REQUEST_ID_CTX.get()


def set_request_id(value: str | None) -> None:
    _REQUEST_ID_CTX.set(value)


class AgentError(BaseModel):
    code: str
    message: str
    problem: str
    cause: str
    fix: str
    docs_url: str | None = None
    retryable: bool = False
    request_id: str | None = None


class AgentErrorException(Exception):
    """Raise inside a route to short-circuit with an AgentError JSON body."""

    def __init__(self, error: AgentError, status_code: int = 400) -> None:
        super().__init__(error.message)
        self.error = error
        self.status_code = status_code


def agent_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AgentErrorException)
    # Fill in request_id from contextvar if the caller didn't set one explicitly.
    payload = exc.error.model_dump()
    if payload.get("request_id") is None:
        payload["request_id"] = current_request_id()
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": payload},
    )


# ---------------------------------------------------------------------------
# AUTH_ error factories. Kept here (rather than inside auth/) so every domain
# that needs to 401 out imports the same place, and so the AgentError payload
# shape is consistent across callers.
# ---------------------------------------------------------------------------


def auth_invalid_credentials() -> AgentErrorException:
    return AgentErrorException(
        AgentError(
            code="AUTH_INVALID_CREDENTIALS",
            message="帳號或密碼錯誤",
            problem="Email or password does not match any active user.",
            cause="Wrong email, wrong password, or the account does not exist.",
            fix="Double-check the credentials. If forgotten, contact an admin — "
            "Phase 1 has no self-serve password reset.",
            retryable=False,
        ),
        status_code=401,
    )


def auth_missing_token() -> AgentErrorException:
    return AgentErrorException(
        AgentError(
            code="AUTH_MISSING_TOKEN",
            message="請先登入",
            problem="Request is missing the Authorization bearer token.",
            cause="No `Authorization: Bearer <jwt>` header was sent.",
            fix="Attach `Authorization: Bearer <access_token>` to the request.",
            retryable=False,
        ),
        status_code=401,
    )


def auth_expired() -> AgentErrorException:
    return AgentErrorException(
        AgentError(
            code="AUTH_EXPIRED",
            message="登入已過期，請重新登入",
            problem="Access token `exp` is in the past.",
            cause="The 15-minute access-token TTL elapsed.",
            fix="Call POST /v1/auth/refresh with the refresh token to mint a new access token.",
            retryable=True,
        ),
        status_code=401,
    )


def auth_invalid_token() -> AgentErrorException:
    return AgentErrorException(
        AgentError(
            code="AUTH_INVALID_TOKEN",
            message="無效的存取權杖",
            problem="Access token signature is invalid, malformed, or uses an unexpected algorithm.",
            cause="Token was tampered with, signed by a different secret, or never minted by this server.",
            fix="Re-authenticate via POST /v1/auth/login.",
            retryable=False,
        ),
        status_code=401,
    )


def auth_refresh_expired() -> AgentErrorException:
    return AgentErrorException(
        AgentError(
            code="AUTH_REFRESH_EXPIRED",
            message="登入過期，請重新登入",
            problem="Refresh token is past its `expires_at`.",
            cause="The 30-day refresh-token TTL elapsed without use.",
            fix="Call POST /v1/auth/login to start a new session.",
            retryable=False,
        ),
        status_code=401,
    )


def auth_refresh_revoked() -> AgentErrorException:
    return AgentErrorException(
        AgentError(
            code="AUTH_REFRESH_REVOKED",
            message="登入已登出，請重新登入",
            problem="Refresh token has been revoked.",
            cause="POST /v1/auth/logout was called for this token, or an admin revoked it.",
            fix="Call POST /v1/auth/login to start a new session.",
            retryable=False,
        ),
        status_code=401,
    )


# ---------------------------------------------------------------------------
# Task-related errors. NOT_FOUND_TASK is also used for "task exists but is
# owned by someone else" — leaking ownership would let agents probe other
# users' job IDs.
# ---------------------------------------------------------------------------


def not_found_task() -> AgentErrorException:
    return AgentErrorException(
        AgentError(
            code="NOT_FOUND_TASK",
            message="找不到此任務",
            problem="No task with the given id is visible to the caller.",
            cause="Either the task id is wrong, the task was already cleaned up "
            "(terminal-state retention is 24h), or the task belongs to another user.",
            fix="Re-fetch the task list via GET /v1/tasks, or verify the id.",
            retryable=False,
        ),
        status_code=404,
    )


def auth_insufficient_permission() -> AgentErrorException:
    return AgentErrorException(
        AgentError(
            code="AUTH_INSUFFICIENT_PERMISSION",
            message="權限不足",
            problem="Caller is authenticated but not allowed to perform this action.",
            cause="The resource is owned by another user; only the owner may modify it.",
            fix="Ask the owner to perform this action, or read-only operations only.",
            retryable=False,
        ),
        status_code=403,
    )


# ---------------------------------------------------------------------------
# Character + creation_session errors. NOT_FOUND_CHARACTER doubles as the
# response for soft-deleted-past-window restore attempts so callers don't
# leak deletion-state metadata via distinct codes.
# ---------------------------------------------------------------------------


def not_found_character() -> AgentErrorException:
    return AgentErrorException(
        AgentError(
            code="NOT_FOUND_CHARACTER",
            message="找不到此角色",
            problem="No character with the given id is visible to the caller, "
            "or the soft-delete restore window has elapsed.",
            cause="Either the id is wrong, the character was hard-deleted, "
            "the character is soft-deleted past the 30-day restore window, "
            "or the character belongs to another team.",
            fix="Re-fetch the list via GET /v1/characters, or verify the id.",
            retryable=False,
        ),
        status_code=404,
    )


def gone_character_restore_window() -> AgentErrorException:
    """Restore-specific 410 — same code as NOT_FOUND_CHARACTER per the
    api-shape error table (404/410 share the prefix). Distinct factory so
    routes can return 410 for past-window restore vs 404 elsewhere
    without leaking which case is which to read-side callers.
    """
    return AgentErrorException(
        AgentError(
            code="NOT_FOUND_CHARACTER",
            message="角色已超過 30 天還原期限",
            problem="Character is soft-deleted but the 30-day restore window has elapsed.",
            cause="Restore was attempted on a row whose `deleted_at` is older than 30 days.",
            fix="The character is no longer recoverable. Re-create from scratch.",
            retryable=False,
        ),
        status_code=410,
    )


def not_found_creation_session() -> AgentErrorException:
    return AgentErrorException(
        AgentError(
            code="NOT_FOUND_CREATION_SESSION",
            message="找不到此建立流程",
            problem="No creation session with the given id is visible to the caller.",
            cause="Either the id is wrong or the session belongs to another team.",
            fix="Re-fetch via GET /v1/characters/{id} to find the active session id.",
            retryable=False,
        ),
        status_code=404,
    )


def conflict_duplicate_name() -> AgentErrorException:
    return AgentErrorException(
        AgentError(
            code="CONFLICT_DUPLICATE_NAME",
            message="此角色名稱已存在",
            problem="A non-deleted character with this name already exists for this owner.",
            cause="Character names are unique per owner (planning/data/db-schema.md §3.3).",
            fix="Pick a different name, or restore / hard-delete the existing one first.",
            retryable=False,
        ),
        status_code=409,
    )


def validation_name_invalid() -> AgentErrorException:
    return AgentErrorException(
        AgentError(
            code="VALIDATION_INVALID_CHARS",
            message="名稱含有不允許的字元",
            problem="Name does not match the required character set: "
            "Chinese (U+4E00–U+9FFF), ASCII letters, digits, underscore, hyphen.",
            cause="Input contains spaces, punctuation, or other unsupported characters.",
            fix="Limit the name to Chinese characters, English letters, digits, `_`, or `-`.",
            retryable=False,
        ),
        status_code=400,
    )


def not_found_checkpoint() -> AgentErrorException:
    """Returned by GET /v1/checkpoints/{id} for unknown ids OR for ids
    whose row hasn't been written yet (worker still running or task
    failed). Frontend doesn't poll this endpoint directly — UI follows
    the task SSE stream — so the missing-row window is invisible to
    real users; agent callers see the standard 404 envelope.
    """
    return AgentErrorException(
        AgentError(
            code="NOT_FOUND_CHECKPOINT",
            message="找不到此候選圖",
            problem="No checkpoint with the given id is visible to the caller, "
            "or the worker has not yet committed the row.",
            cause="Either the id is wrong, the generation task is still in "
            "progress / failed, or the parent session belongs to another team.",
            fix="Subscribe to the create_checkpoint task via "
            "GET /v1/tasks/{task_id}/stream and read the result there.",
            retryable=False,
        ),
        status_code=404,
    )


def not_found_alias() -> AgentErrorException:
    """Returned by `GET / PATCH / DELETE /v1/aliases/{id}` (T-032) when
    the row is unknown to the caller — id never existed, the worker
    hasn't committed it yet, the alias is soft-deleted, or its parent
    character belongs to another team. We collapse all four cases so
    the response can't be used to probe other team's data.
    """
    return AgentErrorException(
        AgentError(
            code="NOT_FOUND_ALIAS",
            message="找不到此造型",
            problem="No alias with the given id is visible to the caller.",
            cause="Either the id is wrong, the alias is soft-deleted, the "
            "worker has not yet committed the row, or the parent character "
            "belongs to another team.",
            fix="Re-fetch via GET /v1/characters/{id}/aliases to find a "
            "current id, or subscribe to the create_alias task via SSE.",
            retryable=False,
        ),
        status_code=404,
    )


def not_found_reference_image() -> AgentErrorException:
    """One or more `reference_image_ids` on a checkpoint create request
    don't exist or don't belong to the session. We collapse both shapes
    to one code so the response can't distinguish "wrong id" from
    "id from a sibling session" — leaking the second would let a
    malicious caller probe other sessions' references."""
    return AgentErrorException(
        AgentError(
            code="NOT_FOUND_REFERENCE_IMAGE",
            message="找不到此參考圖",
            problem="One or more reference_image_ids do not match an upload for this session.",
            cause="The upload id is wrong, was created against a different "
            "session, or the upload row has been cascade-deleted along with "
            "its session.",
            fix="Re-upload via POST /v1/creation-sessions/{id}/reference-images "
            "and use the returned reference_image_id.",
            retryable=False,
        ),
        status_code=404,
    )


def validation_checkpoint_mode() -> AgentErrorException:
    """The `mode` + `base_checkpoint_id` combination on the request body
    is invalid. The constraints (per planning T-017 ticket):
      - retry_same → base_checkpoint_id required
      - remix      → base_checkpoint_id required
      - fresh      → base_checkpoint_id MUST be absent
    """
    return AgentErrorException(
        AgentError(
            code="VALIDATION_CHECKPOINT_MODE",
            message="生成模式與來源候選圖不相符",
            problem="`mode` and `base_checkpoint_id` are inconsistent: "
            "retry_same and remix require a base_checkpoint_id, fresh forbids it.",
            cause="Caller assembled the request body without honoring the "
            "mode-vs-base contract from api-shape §5.2.",
            fix="Send base_checkpoint_id together with retry_same / remix, or omit it for fresh.",
            retryable=False,
        ),
        status_code=400,
    )


def validation_reference_image_required() -> AgentErrorException:
    """`input_mode=reference` sessions can't fire a fresh generation
    without at least one reference image — the whole point of the mode
    is image-conditioning."""
    return AgentErrorException(
        AgentError(
            code="VALIDATION_REFERENCE_IMAGE_REQUIRED",
            message="此模式必須上傳至少一張參考圖",
            problem="The session is in reference input mode but no "
            "reference_image_ids were supplied for a fresh checkpoint.",
            cause="Reference-mode sessions condition every fresh generation on "
            "uploaded imagery; without one there is nothing to vary against.",
            fix="Upload at least one reference image first, then include its "
            "id in reference_image_ids.",
            retryable=False,
        ),
        status_code=400,
    )


def validation_reference_image_unsupported_type() -> AgentErrorException:
    """Reject MIME types other than PNG / JPEG / WebP (T-017 ticket spec)."""
    return AgentErrorException(
        AgentError(
            code="VALIDATION_REFERENCE_IMAGE_TYPE",
            message="參考圖格式不支援，請上傳 PNG / JPEG / WebP",
            problem="Reference image upload has an unsupported MIME type.",
            cause="Phase 1 only supports image/png, image/jpeg, and image/webp.",
            fix="Re-export the reference as PNG, JPEG, or WebP and re-upload.",
            retryable=False,
        ),
        status_code=400,
    )


def validation_reference_image_undecodable() -> AgentErrorException:
    """The uploaded multipart bytes weren't actually a decodable image,
    even though the `Content-Type` header said they were. Catching at
    upload time means the failure surfaces as a 400 next to the upload
    itself instead of a delayed task failure when the worker tries
    `ensure_png_bytes` (Codex P2 round-2)."""
    return AgentErrorException(
        AgentError(
            code="VALIDATION_REFERENCE_IMAGE_UNDECODABLE",
            message="參考圖檔案損毀或格式不正確",
            problem="Reference image upload could not be decoded by PIL.",
            cause="Content-Type claimed PNG / JPEG / WebP but the bytes "
            "were truncated, corrupted, or a different encoding entirely.",
            fix="Re-export the reference from a working image editor and re-upload.",
            retryable=False,
        ),
        status_code=400,
    )


def validation_reference_image_too_large(
    *, size_bytes: int, limit_bytes: int
) -> AgentErrorException:
    return AgentErrorException(
        AgentError(
            code="VALIDATION_REFERENCE_IMAGE_TOO_LARGE",
            message="參考圖檔案過大（上限 10 MB）",
            problem=f"Reference image is {size_bytes} bytes; the limit is {limit_bytes} bytes.",
            cause="Phase 1 caps reference uploads at 10 MB to bound storage "
            "and worker memory pressure.",
            fix="Compress the reference (lower resolution, optimise PNG, or "
            "convert to WebP) and re-upload.",
            retryable=False,
        ),
        status_code=400,
    )


def queue_unavailable(*, task_id: str | None = None) -> AgentErrorException:
    """Raised when the arq queue can't accept a job — usually Redis is
    down or unreachable. The DB row has already been marked `failed`
    with the same code by `task_service.create_task`; we surface this
    AgentError to the caller so the response is structured (not a 500)
    and includes the reserved `task_id` so the caller can still inspect
    the failed row (Codex P1 round-7).
    """
    return AgentErrorException(
        AgentError(
            code="QUEUE_UNAVAILABLE",
            message="任務佇列暫時不可用，請稍後再試",
            problem="arq enqueue_job raised; the task row was marked "
            "failed to avoid a stuck queued orphan." + (f" Task id: {task_id}." if task_id else ""),
            cause="Redis or the arq worker pool is unreachable.",
            fix="Retry shortly. If the issue persists, check infra / "
            "Redis status and the worker process.",
            retryable=True,
        ),
        status_code=503,
    )


def conflict_session_not_active() -> AgentErrorException:
    """Mutating endpoints (`/checkpoints`, `/reference-images`) refuse
    to act on a session that's already `completed` or `abandoned` —
    the session's lifecycle is supposed to end the moment Base is
    selected or the user gives up."""
    return AgentErrorException(
        AgentError(
            code="CONFLICT_SESSION_NOT_ACTIVE",
            message="此建立流程已結束，無法繼續",
            problem="The creation session is no longer in_progress; "
            "no further checkpoints or references can be added.",
            cause="The caller attempted to mutate a session that has been "
            "completed (Base selected) or abandoned.",
            fix="Start a new creation session via POST /v1/characters.",
            retryable=False,
        ),
        status_code=409,
    )


def conflict_sequence_race() -> AgentErrorException:
    """A checkpoint INSERT collided with the `(creation_session_id,
    sequence)` UNIQUE constraint. Phase 1 accepts this as a rare race
    between Redis recovery and DB commit (planning §3.5 殘餘 race) and
    surfaces it as retryable so the user just hits "重試"."""
    return AgentErrorException(
        AgentError(
            code="CONFLICT_SEQUENCE_RACE",
            message="生成衝突，請重試",
            problem="Checkpoint INSERT failed on the (creation_session_id, "
            "sequence) UNIQUE constraint.",
            cause="The Redis sequence allocator and a concurrent worker "
            "raced; both reserved the same sequence value before the row "
            "committed (planning/backend/task-queue.md §3.5).",
            fix="Retry the request; the allocator will re-reserve a new sequence on the next call.",
            retryable=True,
        ),
        status_code=409,
    )


def conflict_base_locked() -> AgentErrorException:
    """Returned by select-base / abandon when the session has already
    been completed (a Base row exists). Phase 1 Base is immutable —
    you can't re-pick or undo by abandoning. Surface a distinct code
    from CONFLICT_SESSION_NOT_ACTIVE so the frontend can offer a
    different remediation ("Base 已確立" → go to character detail)
    rather than the generic "start a new session" copy.
    """
    return AgentErrorException(
        AgentError(
            code="CONFLICT_BASE_LOCKED",
            message="此角色的基礎形象已確立，無法重新選擇或放棄",
            problem="The creation session is already completed; "
            "a Base row exists for the character.",
            cause="Base is immutable in Phase 1. To change the look you "
            "must delete the character and start over.",
            fix="Open the character detail page to view or edit the existing Base.",
            retryable=False,
        ),
        status_code=409,
    )


def conflict_duplicate_alias_name() -> AgentErrorException:
    """Alias names are unique per character (planning/data/db-schema.md
    §3.7 partial UNIQUE). Mirrors `conflict_duplicate_name` in shape
    but keeps a distinct code so the frontend can render a more
    targeted "造型名稱已存在" hint instead of conflating with character
    naming."""
    return AgentErrorException(
        AgentError(
            code="CONFLICT_DUPLICATE_NAME",
            message="此造型名稱已存在",
            problem="A non-deleted alias with this name already exists for this character.",
            cause="Alias names are unique per character (planning/data/db-schema.md §3.7).",
            fix="Pick a different name, or restore / hard-delete the existing alias first.",
            retryable=False,
        ),
        status_code=409,
    )


def validation_alias_empty_input() -> AgentErrorException:
    """Alias-create body provided no usable signal: no freeform_note,
    no reference_image_ids, no mask. Same code as the prompt-preview
    surface (VALIDATION_EMPTY_INPUT) so callers can share a single
    handler — the alias-create matrix is a stricter superset of the
    preview rule.
    """
    return AgentErrorException(
        AgentError(
            code="VALIDATION_EMPTY_INPUT",
            message="請至少提供補述、參考圖或 inpaint 範圍其一",
            problem="Alias create body has no freeform_note, reference_image_ids, or mask.",
            cause="At least one input signal is required so the worker has "
            "something to condition the AI call on.",
            fix="Populate one of freeform_note / reference_image_ids / mask before retrying.",
            retryable=False,
        ),
        status_code=422,
    )


def conflict_task_already_terminal() -> AgentErrorException:
    return AgentErrorException(
        AgentError(
            code="CONFLICT_TASK_ALREADY_TERMINAL",
            message="任務已結束，無法取消",
            problem="Task is already in a terminal state (completed/failed/cancelled) "
            "and cancel was previously acknowledged.",
            cause="Cancel was called on a task that has nothing left to cancel.",
            fix="Inspect the task via GET /v1/tasks/{id} for the final result or error.",
            retryable=False,
        ),
        status_code=409,
    )
