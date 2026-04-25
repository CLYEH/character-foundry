"""Env-parsing helpers in `app.ai.config` (T-014)."""

from __future__ import annotations

import pytest

from app.ai import config


def test_max_retries_allows_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """Codex P2 round-2: operators must be able to disable retries via
    `GPT_IMAGE_2_MAX_RETRIES=0` for incident response or load tests.
    The previous `value > 0` guard silently fell back to the default,
    forcing a code change to disable retries.
    """
    monkeypatch.setenv("GPT_IMAGE_2_MAX_RETRIES", "0")
    assert config.gpt_image_2_max_retries() == 0


def test_max_retries_default_when_env_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GPT_IMAGE_2_MAX_RETRIES", raising=False)
    assert config.gpt_image_2_max_retries() == 3


def test_max_retries_default_on_garbage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GPT_IMAGE_2_MAX_RETRIES", "not-a-number")
    assert config.gpt_image_2_max_retries() == 3


def test_max_retries_rejects_negative(monkeypatch: pytest.MonkeyPatch) -> None:
    """Negative values are still nonsense — fall back to default."""
    monkeypatch.setenv("GPT_IMAGE_2_MAX_RETRIES", "-1")
    assert config.gpt_image_2_max_retries() == 3


def test_timeout_still_rejects_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """Other knobs keep their `must be positive` semantic — a 0ms timeout
    would mean "fail every call instantly", which is never what an
    operator means."""
    monkeypatch.setenv("GPT_IMAGE_2_TIMEOUT_MS", "0")
    # Default 60_000ms → 60s.
    assert config.gpt_image_2_timeout_seconds() == 60.0


def test_circuit_threshold_still_rejects_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_CIRCUIT_FAILURE_THRESHOLD", "0")
    assert config.circuit_failure_threshold() == 5
