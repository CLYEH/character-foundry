"""AI-client tuning knobs read from environment variables.

Centralised here (rather than a project-wide settings module) so the AI
package stays self-contained and tests can monkey-patch a single import
location. Each accessor is a function so env mutations between tests are
honoured without restarting the process.

See planning/devops/environment-variables.md §2.2.
"""

from __future__ import annotations

import os


def _bool_env(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


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


# Circuit-breaker tuning. Values default to ai-integration.md §3.4 numbers.
def circuit_failure_threshold() -> int:
    return _int_env("AI_CIRCUIT_FAILURE_THRESHOLD", default=5)


def circuit_failure_window_seconds() -> int:
    return _int_env("AI_CIRCUIT_FAILURE_WINDOW_SECONDS", default=60)


def circuit_open_duration_seconds() -> int:
    return _int_env("AI_CIRCUIT_OPEN_DURATION_SECONDS", default=300)
