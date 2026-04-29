"""Prompt-reconciler error factories (T-015).

Map each failure mode in planning/backend/prompt-reconciler.md §8 onto a
distinct AgentError code so workers and the prompt preview endpoint return
something a UI / agent can act on:

  - `PROMPT_CONFLICT`         — LLM returned an output that doesn't match the
    required schema (or, by extension, couldn't reconcile a real conflict).
    Treated as user-input / system-prompt fault, not retryable from the
    caller's perspective.
  - `PROMPT_RECONCILE_FAILED` — transient downstream failure that retried
    and still didn't yield JSON; caller may retry.

`PROMPT_CONTENT_POLICY` is already provided by `app.ai.errors` — reuse it
when the LLM provider rejects a prompt under their safety filters.
"""

from __future__ import annotations

from app.core.errors import AgentError, AgentErrorException


def prompt_conflict(
    *, problem: str, cause: str | None = None, fix: str | None = None
) -> AgentErrorException:
    return AgentErrorException(
        AgentError(
            code="PROMPT_CONFLICT",
            message="使用者補述與平台 constraints 衝突，已自動修正",
            problem=problem,
            cause=cause or "User input conflicts with platform-level image constraints.",
            fix=fix
            or "Remove background-related keywords from freeform note, "
            "or accept auto-reconciled prompt.",
            retryable=False,
        ),
        status_code=400,
    )


def prompt_reconcile_failed(*, cause: str | None = None) -> AgentErrorException:
    return AgentErrorException(
        AgentError(
            code="PROMPT_RECONCILE_FAILED",
            message="提示詞調和失敗，請稍後再試",
            problem="Prompt reconciliation could not produce a valid output.",
            cause=cause
            or "Reconciler LLM repeatedly returned invalid JSON or hit transient errors.",
            fix="Retry shortly; if persistent the circuit breaker will fall back to a "
            "degraded path (constraints + freeform note untranslated).",
            retryable=True,
        ),
        status_code=502,
    )


def validation_empty_input() -> AgentErrorException:
    """`POST /v1/prompt/preview` body has no signal to reconcile.

    The reconciler still returns a valid output for an all-empty input
    (constraints alone), but the endpoint guards against it because a
    "preview with nothing" is almost always a frontend bug — the user
    pressed 進階檢視 before filling anything in. Surfacing it as 400
    keeps the contract honest.
    """
    return AgentErrorException(
        AgentError(
            code="VALIDATION_EMPTY_INPUT",
            message="請至少提供選單、補述、參考圖或 inpaint 範圍其一",
            problem="Prompt preview was called with no menu_selections, "
            "freeform_note, reference_image_ids, or mask.",
            cause="At least one input signal is required to compose a meaningful prompt.",
            fix="Populate one of menu_selections / freeform_note / "
            "reference_image_ids / mask before calling preview.",
            retryable=False,
        ),
        status_code=400,
    )


def validation_mask_required() -> AgentErrorException:
    """`mask` was supplied as an empty / shapeless payload.

    The wire schema is `{ mask_id: UUID }`; an empty object or one
    missing `mask_id` reaches the route as a malformed `mask`. Per
    T-035 we surface this as a structured `VALIDATION_MASK_REQUIRED`
    422 instead of letting Pydantic emit the generic
    `RequestValidationError` body — frontend uses the code to render
    a "請先設定遮罩" hint inline rather than a generic 422.
    """
    return AgentErrorException(
        AgentError(
            code="VALIDATION_MASK_REQUIRED",
            message="請先設定要編修的範圍（mask）",
            problem="`mask` was supplied without a valid `mask_id`.",
            cause="The wire shape for `mask` is `{ mask_id: UUID }`; an "
            "empty object or a non-UUID value cannot be resolved to an "
            "uploaded mask.",
            fix="Upload the mask first (POST .../aliases/masks) and "
            "send the returned id as `{ mask: { mask_id } }`.",
            retryable=False,
        ),
        status_code=422,
    )


def not_found_mask() -> AgentErrorException:
    """The supplied `mask_id` doesn't resolve to a mask the caller can see.

    Same opacity as `NOT_FOUND_REFERENCE_IMAGE`: a mask owned by a
    different character collapses to the same 404 as a wrong id, so
    callers can't probe for other characters' uploads via mask-id
    enumeration.
    """
    return AgentErrorException(
        AgentError(
            code="NOT_FOUND_MASK",
            message="找不到此遮罩",
            problem="The supplied mask_id does not match any uploaded mask the caller can see.",
            cause="Either the mask_id is wrong, the mask belongs to "
            "another character / team, or the mask was cascade-deleted "
            "with its character.",
            fix="Re-upload the mask via the alias mask upload endpoint "
            "and use the freshly returned id.",
            retryable=False,
        ),
        status_code=404,
    )


def not_found_alias() -> AgentErrorException:
    """The supplied alias parent doesn't resolve to an alias the caller can see.

    Mirrors `NOT_FOUND_CHARACTER`: visibility is gated on team
    ownership (and, for write paths, character owner), so a wrong id
    and a sibling-team id collapse to the same 404.
    """
    return AgentErrorException(
        AgentError(
            code="NOT_FOUND_ALIAS",
            message="找不到此造型",
            problem="No alias with the given id is visible to the caller.",
            cause="Either the id is wrong, the alias was soft-deleted, "
            "or the alias belongs to another team's character.",
            fix="Re-fetch the alias list via GET /v1/characters/{id}/aliases.",
            retryable=False,
        ),
        status_code=404,
    )


def validation_motion_parent_mismatch() -> AgentErrorException:
    """`parent_type` doesn't match the row that `parent_id` points at.

    A motion preview body says e.g. `parent_type='base'` but
    `parent_id` is actually an alias id. Distinct from
    `NOT_FOUND_*` because the row exists — it's just the wrong kind.
    """
    return AgentErrorException(
        AgentError(
            code="VALIDATION_MOTION_PARENT_MISMATCH",
            message="動作的來源類型與 ID 不一致",
            problem="`parent_type` does not match the row referenced by "
            "`parent_id` (e.g. parent_type='base' but the id is an alias).",
            cause="Caller assembled the motion preview body with a mismatched parent type/id pair.",
            fix="Send the parent_type that matches the parent_id — "
            "'base' for a base id, 'alias' for an alias id.",
            retryable=False,
        ),
        status_code=400,
    )


def conflict_base_not_set() -> AgentErrorException:
    """Alias / motion preview was called for a character without a Base.

    Distinct from `NOT_FOUND_CHARACTER` so the frontend can render
    "請先確立基礎形象" rather than the generic "character not found"
    copy. T-031's alias-create route will raise the same code at write
    time; T-035 surfaces it on the read path so the modal can guide
    users back to Select Base.
    """
    return AgentErrorException(
        AgentError(
            code="CONFLICT_BASE_NOT_SET",
            message="請先確立角色的基礎形象",
            problem="The character does not yet have a Base — alias / motion "
            "previews are unreachable until Select Base completes.",
            cause="The creation session for this character is still in_progress "
            "or was abandoned without selecting a Base.",
            fix="Open the character's creation session and pick a Base "
            "checkpoint, then retry the alias / motion preview.",
            retryable=False,
        ),
        status_code=409,
    )


def validation_motion_custom_requires_description() -> AgentErrorException:
    """`motion_type='custom'` was supplied without a description.

    Mirrors the DB CHECK constraint on `motions.description`: custom
    motions need user-supplied prompt text; presets don't.
    """
    return AgentErrorException(
        AgentError(
            code="VALIDATION_MOTION_DESCRIPTION_REQUIRED",
            message="自訂動作必須填寫描述",
            problem="`motion_type='custom'` was supplied without a non-empty `description`.",
            cause="Custom motions get their prompt from `description`; "
            "preset motions read a static template, so they don't.",
            fix="Send `description` together with `motion_type='custom'`, "
            "or pick a preset motion_type.",
            retryable=False,
        ),
        status_code=422,
    )
