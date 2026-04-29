"""AI-client tuning knobs read from environment variables.

Centralised here (rather than a project-wide settings module) so the AI
package stays self-contained and tests can monkey-patch a single import
location. Each accessor is a function so env mutations between tests are
honoured without restarting the process.

See planning/devops/environment-variables.md §2.2.
"""

from __future__ import annotations

import os

_TRUE_TOKENS = frozenset({"1", "true", "yes", "on"})
_FALSE_TOKENS = frozenset({"0", "false", "no", "off"})


def _bool_env(name: str, *, default: bool) -> bool:
    """Parse a bool env var; fall back to `default` for missing OR invalid values.

    Codex P1 round-3: a typo like `AI_STUB_MODE=treu` previously slipped
    silently into the "anything not truthy is False" branch, flipping the
    safe stub default off and surfacing as either an unintended OpenAI
    spend or a hard failure if the key was unset. Now only explicit false
    tokens disable, and anything else (typo, garbage, mixed-case Yes) keeps
    the documented default.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalised = raw.strip().lower()
    if normalised in _TRUE_TOKENS:
        return True
    if normalised in _FALSE_TOKENS:
        return False
    return default


def _int_env(name: str, *, default: int, min_value: int = 1) -> int:
    """Parse an int env var; fall back to `default` on missing / garbage / below min.

    `min_value` guards against operationally-meaningless zeros for things
    like timeouts, but callers that *want* zero (e.g. retries-disabled)
    pass `min_value=0` explicitly. Codex P2 round-2.
    """
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= min_value else default


def stub_mode_enabled() -> bool:
    """`AI_STUB_MODE=true` swaps the real client for the fixture-backed stub.

    Default is True so unit tests / CI / local dev never charge against the
    OpenAI account. Production explicitly sets `AI_STUB_MODE=false`.
    """
    return _bool_env("AI_STUB_MODE", default=True)


def openai_api_key() -> str | None:
    return os.environ.get("OPENAI_API_KEY")


def gpt_image_2_model() -> str:
    return os.environ.get("GPT_IMAGE_2_MODEL", "gpt-image-2")


def gpt_image_2_timeout_seconds() -> float:
    return _int_env("GPT_IMAGE_2_TIMEOUT_MS", default=60_000) / 1000.0


def gpt_image_2_max_retries() -> int:
    """Retry attempts after the initial call (so total = retries + 1).

    Allows `0` so operators can disable retries during incidents / load tests
    without code changes (Codex P2 round-2).
    """
    return _int_env("GPT_IMAGE_2_MAX_RETRIES", default=3, min_value=0)


def openai_api_base() -> str:
    return os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1")


# Reconciler (gpt-5-mini) tuning — shared OPENAI_API_KEY with gpt-image-2.
# Defaults match planning/backend/prompt-reconciler.md §4 and
# planning/devops/environment-variables.md §2.2.
def reconciler_model() -> str:
    return os.environ.get("RECONCILER_MODEL", "gpt-5-mini")


def reconciler_timeout_seconds() -> float:
    return _int_env("RECONCILER_TIMEOUT_MS", default=30_000) / 1000.0


def reconciler_max_retries() -> int:
    """Retry attempts after the initial call (so total = retries + 1).

    Allows `0` so operators can disable retries during incidents without
    code changes (matches the gpt-image-2 knob).
    """
    return _int_env("RECONCILER_MAX_RETRIES", default=3, min_value=0)


def reconciler_max_tokens() -> int:
    return _int_env("RECONCILER_MAX_TOKENS", default=800)


# Veo 3.1 (i2v) tuning — see planning/devops/environment-variables.md §2.2 and
# planning/backend/ai-integration.md §4. `VEO_API_URL` is required for the real
# client; `AI_STUB_MODE=true` makes it optional in dev / CI.
def veo_api_key() -> str | None:
    return os.environ.get("VEO_API_KEY")


def veo_api_url() -> str:
    return os.environ.get("VEO_API_URL", "https://generativelanguage.googleapis.com/v1beta")


def veo_model() -> str:
    return os.environ.get("VEO_MODEL", "veo-3.1")


def veo_timeout_seconds() -> float:
    """Per-HTTP-request timeout (submit / single-poll / download). Veo's
    long-running operation can take minutes overall, but each individual
    HTTP call should respond quickly; the polling loop sleeps between
    polls rather than holding one long socket open.
    """
    return _int_env("VEO_TIMEOUT_MS", default=180_000) / 1000.0


def veo_max_retries() -> int:
    """Retry attempts after the initial submission (so total = retries + 1).

    Default 2 because video generation is expensive — failed submissions
    burn provider quota even when they don't return bytes. Allows `0` so
    operators can disable retries during incidents (matches the gpt-image-2
    knob).
    """
    return _int_env("VEO_MAX_RETRIES", default=2, min_value=0)


def veo_poll_interval_seconds() -> float:
    """Sleep between successive `GET /operations/{name}` polls. Defaults
    to 5s per ai-integration.md §4.2 — Veo doesn't surface progress %, so
    the worker estimates progress separately (see task-queue.md §5.2).
    """
    return _int_env("VEO_POLL_INTERVAL_MS", default=5_000) / 1000.0


def veo_max_poll_attempts() -> int:
    """Cap the polling loop so a stuck operation doesn't block a worker
    forever. Default 60 polls × 5s = 5 min; matches `VEO_TIMEOUT_MS`
    default. After this we raise `MODEL_TIMEOUT` and let the breaker /
    retry layer decide what to do next.
    """
    return _int_env("VEO_MAX_POLL_ATTEMPTS", default=60, min_value=1)


# Circuit-breaker tuning. Values default to ai-integration.md §3.4 numbers.
def circuit_failure_threshold() -> int:
    return _int_env("AI_CIRCUIT_FAILURE_THRESHOLD", default=5)


def circuit_failure_window_seconds() -> int:
    return _int_env("AI_CIRCUIT_FAILURE_WINDOW_SECONDS", default=60)


def circuit_open_duration_seconds() -> int:
    return _int_env("AI_CIRCUIT_OPEN_DURATION_SECONDS", default=300)
