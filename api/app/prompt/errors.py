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
