"""Factory `get_image_client` honours `AI_STUB_MODE` (T-014)."""

from __future__ import annotations

import fakeredis.aioredis
import pytest

from app.ai.factory import get_image_client
from app.ai.gpt_image_2 import GptImage2Client
from app.ai.stub import StubAIClient


def test_factory_returns_stub_when_AI_STUB_MODE_true(
    fake_redis: fakeredis.aioredis.FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_STUB_MODE", "true")
    client = get_image_client(fake_redis)
    assert isinstance(client, StubAIClient)


def test_factory_returns_real_client_when_AI_STUB_MODE_false(
    fake_redis: fakeredis.aioredis.FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_STUB_MODE", "false")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    client = get_image_client(fake_redis)
    assert isinstance(client, GptImage2Client)


def test_factory_force_stub_overrides_env(
    fake_redis: fakeredis.aioredis.FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_STUB_MODE", "false")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    client = get_image_client(fake_redis, force_stub=True)
    assert isinstance(client, StubAIClient)


def test_factory_default_is_stub_mode(
    fake_redis: fakeredis.aioredis.FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 1 dev/CI default — never charge the OpenAI account by accident."""
    monkeypatch.delenv("AI_STUB_MODE", raising=False)
    client = get_image_client(fake_redis)
    assert isinstance(client, StubAIClient)
