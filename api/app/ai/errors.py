"""Provider-error → `AgentError` mapping (T-014).

The model-call layer only knows two outcomes: success or `AgentErrorException`
with a code from api-shape.md §4.1 (`MODEL_*` / `PROMPT_*` / `INTERNAL_*`).
Centralising the translation here keeps the codes consistent across the
real client, the circuit breaker, and any future provider.

Each factory mirrors the auth_* helpers in `app/core/errors.py`: they fill
in the full AgentError envelope so callers `raise <fn>()` without having to
remember the schema.
"""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from app.core.errors import AgentError, AgentErrorException

# ---------------------------------------------------------------------------
# Factories — one per code we emit. Status codes follow api-shape.md §4.1.
# ---------------------------------------------------------------------------


def model_timeout(model: str, *, cause: str | None = None) -> AgentErrorException:
    return AgentErrorException(
        AgentError(
            code="MODEL_TIMEOUT",
            message="模型回應逾時，請稍後再試",
            problem=f"Upstream call to {model} exceeded the client timeout.",
            cause=cause or "Provider response did not arrive before the configured timeout.",
            fix="Retry after a brief pause; if persistent the circuit breaker will trip.",
            retryable=True,
        ),
        status_code=504,
    )


def model_rate_limit(model: str, *, retry_after: float | None = None) -> AgentErrorException:
    fix = (
        "Wait at least the value reported by the provider before retrying."
        if retry_after is None
        else f"Retry after {retry_after:.1f}s — the provider asked us to back off."
    )
    return AgentErrorException(
        AgentError(
            code="MODEL_RATE_LIMIT",
            message="模型暫時超出流量限制，請稍後再試",
            problem=f"Provider {model} returned 429 Too Many Requests.",
            cause="Per-model rate limit exhausted on the provider side.",
            fix=fix,
            retryable=True,
        ),
        status_code=429,
    )


def model_unavailable(model: str, *, cause: str | None = None) -> AgentErrorException:
    return AgentErrorException(
        AgentError(
            code="MODEL_UNAVAILABLE",
            message="模型暫時不可用，請稍後再試",
            problem=f"{model} is currently unavailable (circuit OPEN or upstream 5xx).",
            cause=cause or "Repeated upstream failures tripped the circuit breaker.",
            fix="Wait until the circuit closes (typically 5 minutes) and retry.",
            retryable=True,
        ),
        status_code=502,
    )


def prompt_content_policy(model: str) -> AgentErrorException:
    # We deliberately do NOT echo the provider's rejection reason — it can
    # leak internal moderation taxonomy (planning §3.5).
    return AgentErrorException(
        AgentError(
            code="PROMPT_CONTENT_POLICY",
            message="內容涉及限制主題，請修改補述後重試",
            problem=f"{model} rejected the prompt under its content policy.",
            cause="Prompt or supplied imagery hit the provider's safety filters.",
            fix="Edit the freeform note to remove restricted content and retry.",
            retryable=False,
        ),
        status_code=400,
    )


def internal_auth_failed(model: str, *, key_env_var: str = "OPENAI_API_KEY") -> AgentErrorException:
    """Surface a provider auth rejection (401/403) with the right env-var name.

    `key_env_var` defaults to `OPENAI_API_KEY` (used by gpt-image-2 and the
    reconciler) but Veo callers pass `VEO_API_KEY` so on-call sees the
    correct credential to rotate (Codex P2 round-5 on PR #39).
    """
    return AgentErrorException(
        AgentError(
            code="INTERNAL_AUTH_FAILED",
            message="系統設定錯誤，請聯絡管理員",
            problem=f"{model} provider rejected the API key (401/403).",
            cause=f"{key_env_var} is missing, expired, or lacks the required scopes.",
            fix=f"Operations: rotate {key_env_var} and restart the API service.",
            retryable=False,
        ),
        status_code=500,
    )


def model_response_truncated(model: str, *, detail: str | None = None) -> AgentErrorException:
    """Provider returned a 200 with a truncated body (e.g. Chat Completions
    `finish_reason=length`). Non-retryable: same input + same max_tokens
    will deterministically truncate again, so retrying just burns the
    breaker budget. Surface as MODEL_INVALID_REQUEST to stay within the
    api-shape.md §4 categories.
    """
    return AgentErrorException(
        AgentError(
            code="MODEL_INVALID_REQUEST",
            message="模型回應被截斷，請縮短輸入或調高 max_tokens",
            problem=f"{model} truncated its response (finish_reason=length).",
            cause=detail or "Token budget exhausted before the model finished.",
            fix="Increase the model's max_tokens (e.g. RECONCILER_MAX_TOKENS) "
            "or shorten the input prompt.",
            retryable=False,
        ),
        status_code=502,
    )


def model_quota_exceeded(model: str, *, detail: str | None = None) -> AgentErrorException:
    """Provider returned a hard quota / billing exhaustion signal (e.g. Veo's
    `RESOURCE_EXHAUSTED` per planning §4.4). Distinct from `MODEL_RATE_LIMIT`
    because rate limits clear in seconds while quota exhaustion needs an
    operator action (top-up / billing fix). Non-retryable so the worker
    surfaces the specific code rather than burning further provider budget.
    """
    return AgentErrorException(
        AgentError(
            code="MODEL_QUOTA_EXCEEDED",
            message="模型額度已用完，請聯絡管理員",
            problem=f"{model} reported quota / resource exhaustion."
            + (f" Detail: {detail}" if detail else ""),
            cause="Project-level quota or billing limit reached on the provider side.",
            fix="Operations: top up the provider account or raise the per-project quota; "
            "retrying without that change will not succeed.",
            retryable=False,
        ),
        status_code=429,
    )


def model_invalid_request(model: str, *, detail: str | None = None) -> AgentErrorException:
    return AgentErrorException(
        AgentError(
            code="MODEL_INVALID_REQUEST",
            message="模型輸入不合法，請重新嘗試",
            problem=f"{model} returned 4xx for the request payload."
            + (f" Detail: {detail}" if detail else ""),
            cause="Client-side payload mismatched the provider's schema "
            "(unsupported size, malformed image, etc).",
            fix="Inspect the request payload; this is a bug if the input looked valid.",
            retryable=False,
        ),
        status_code=502,
    )


# ---------------------------------------------------------------------------
# Translator — converts an httpx error / response into an AgentErrorException.
# Centralised so both the real client and any future provider share one truth.
# ---------------------------------------------------------------------------


def _looks_like_content_policy(payload: Any) -> bool:
    """Best-effort detector for OpenAI-style content-policy rejections.

    OpenAI returns a 400 with `error.code` of `content_policy_violation`
    or `error.type` containing `content_policy`. Tolerate either shape.
    """
    if not isinstance(payload, dict):
        return False
    err = payload.get("error")
    if not isinstance(err, dict):
        return False
    for field in ("code", "type", "param"):
        value = err.get(field)
        if isinstance(value, str) and "content_policy" in value:
            return True
    message = err.get("message")
    return isinstance(message, str) and "content policy" in message.lower()


def map_response_to_agent_error(
    model: str,
    response: httpx.Response,
    *,
    auth_key_env_var: str = "OPENAI_API_KEY",
) -> AgentErrorException:
    """Translate a non-2xx HTTP response into the matching AgentError.

    Tolerates non-JSON bodies (e.g. HTML 502 from a load balancer) by
    falling back to status-code routing only. `auth_key_env_var` lets the
    caller (e.g. the Veo client) tell `internal_auth_failed` which env
    variable to name in the operator-facing remediation.
    """
    status = response.status_code
    payload: Any = None
    try:
        payload = response.json()
    except (ValueError, TypeError):
        payload = None

    if status == 429:
        retry_after = parse_retry_after_seconds(response.headers.get("Retry-After"))
        return model_rate_limit(model, retry_after=retry_after)

    if status in (401, 403):
        return internal_auth_failed(model, key_env_var=auth_key_env_var)

    if status == 400 and _looks_like_content_policy(payload):
        return prompt_content_policy(model)

    if 400 <= status < 500:
        detail = _extract_message(payload)
        return model_invalid_request(model, detail=detail)

    # 5xx → unavailable. The breaker counts these toward OPEN, so callers
    # that retry will eventually short-circuit before bombarding the
    # provider further.
    return model_unavailable(model, cause=f"HTTP {status} from provider")


def map_exception_to_agent_error(model: str, exc: Exception) -> AgentErrorException:
    """Translate a raised httpx exception (timeout / transport error) into AgentError."""
    if isinstance(exc, httpx.TimeoutException):
        return model_timeout(model, cause=str(exc) or None)
    if isinstance(exc, httpx.HTTPError):
        return model_unavailable(model, cause=f"transport error: {exc}")
    # Anything else bubbles as a generic unavailable so callers don't have
    # to special-case every transport library.
    return model_unavailable(model, cause=f"unexpected error: {exc}")


def parse_retry_after_seconds(raw: str | None) -> float | None:
    """Parse an HTTP `Retry-After` header into a non-negative seconds value.

    Per RFC 9110 §10.2.3 the header value is either a `delta-seconds`
    integer OR an HTTP-date. Codex P2 round-3: the previous parser only
    handled the numeric form, so a date-formatted Retry-After silently
    fell through to exponential backoff and retried earlier than the
    server asked, increasing the chance of repeat 429s and unnecessary
    breaker pressure.

    Returns None for missing / unparseable values; callers fall back to
    their own backoff policy.
    """
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return None
    # Form 1: delta-seconds.
    try:
        return max(float(raw), 0.0)
    except (TypeError, ValueError):
        pass
    # Form 2: HTTP-date. parsedate_to_datetime returns naive datetimes for
    # values that omit a timezone — RFC requires GMT, so default-attach UTC.
    try:
        target = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if target is None:
        return None
    if target.tzinfo is None:
        target = target.replace(tzinfo=UTC)
    delta = (target - datetime.now(tz=UTC)).total_seconds()
    return max(delta, 0.0)


def _extract_message(payload: Any) -> str | None:
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict):
            msg = err.get("message")
            if isinstance(msg, str):
                return msg
        msg = payload.get("message")
        if isinstance(msg, str):
            return msg
    return None
