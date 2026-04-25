"""Gpt5MiniClient behaviour against a mocked OpenAI Chat Completions API (T-015).

Coverage focus: the parsing surface and circuit-breaker integration that
diverge from GptImage2Client (chat-style payload, JSON-mode content
extraction). Shared retry / mapping behaviour is already exercised by
`test_gpt_image_2.py` and `test_errors.py`.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import fakeredis.aioredis
import httpx
import pytest

from app.ai.reconciler_client import (
    RECONCILER_SERVICE_NAME,
    Gpt5MiniClient,
    StubReconcilerClient,
)
from app.core.errors import AgentErrorException


def _make_client(
    fake_redis: fakeredis.aioredis.FakeRedis,
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    max_retries: int = 0,
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> Gpt5MiniClient:
    if monkeypatch is not None:

        async def _no_sleep(_seconds: float) -> None:
            return None

        monkeypatch.setattr("asyncio.sleep", _no_sleep)

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        transport=transport,
        base_url="https://api.openai.test/v1",
        headers={"Authorization": "Bearer test-key"},
    )
    return Gpt5MiniClient(
        redis=fake_redis,
        api_key="test-key",
        api_base="https://api.openai.test/v1",
        model="gpt-5-mini",
        timeout_seconds=2.0,
        max_retries=max_retries,
        max_tokens=400,
        http_client=http_client,
    )


def _chat_response(body: dict[str, object], *, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=body)


def _wrap_json_content(payload: dict[str, object]) -> dict[str, object]:
    """OpenAI returns the JSON-mode result as a string in `message.content`."""
    return {
        "model": "gpt-5-mini",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": json.dumps(payload),
                }
            }
        ],
    }


async def test_call_returns_parsed_chat_json_payload(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    expected = {"reconciled_note_en": "hello", "removed_segments": []}

    def _handler(_request: httpx.Request) -> httpx.Response:
        return _chat_response(_wrap_json_content(expected))

    client = _make_client(fake_redis, _handler)
    try:
        result = await client.call(system_prompt="sys", user_prompt="user")
    finally:
        await client.aclose()

    assert result == expected


async def test_call_targets_chat_completions_path_with_v1_prefix(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    captured: dict[str, str] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["host"] = request.url.host
        return _chat_response(
            _wrap_json_content({"reconciled_note_en": "", "removed_segments": []})
        )

    client = _make_client(fake_redis, _handler)
    try:
        await client.call(system_prompt="sys", user_prompt="user")
    finally:
        await client.aclose()

    assert captured["host"] == "api.openai.test"
    assert captured["path"] == "/v1/chat/completions"


async def test_invalid_chat_content_string_raises_agent_error(
    fake_redis: fakeredis.aioredis.FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`response_format: json_object` should keep us safe, but if the
    provider ever ships malformed text we treat it as MODEL_UNAVAILABLE
    rather than crashing the worker."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return _chat_response(
            {
                "model": "gpt-5-mini",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "<<not json>>",
                        }
                    }
                ],
            }
        )

    client = _make_client(fake_redis, _handler, monkeypatch=monkeypatch)
    try:
        with pytest.raises(AgentErrorException) as info:
            await client.call(system_prompt="sys", user_prompt="user")
    finally:
        await client.aclose()
    assert info.value.error.code == "MODEL_UNAVAILABLE"


async def test_5xx_failures_open_circuit_under_reconciler_service_name(
    fake_redis: fakeredis.aioredis.FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaker key is `circuit:reconciler:*` / `degraded:reconciler` — the
    service name not the SKU, so /v1/meta surfaces a stable identifier."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return _chat_response({"error": {"message": "down"}}, status=503)

    client = _make_client(fake_redis, _handler, max_retries=0, monkeypatch=monkeypatch)
    try:
        for _ in range(5):
            with pytest.raises(AgentErrorException):
                await client.call(system_prompt="sys", user_prompt="user")
    finally:
        await client.aclose()

    raw = await fake_redis.get(f"degraded:{RECONCILER_SERVICE_NAME}")
    assert raw is not None
    payload = json.loads(raw)
    assert payload["reason"] == "CIRCUIT_OPEN"


async def test_stub_reconciler_returns_schema_valid_empty_payload() -> None:
    stub = StubReconcilerClient()
    result = await stub.call(system_prompt="sys", user_prompt="user")
    assert result == {"reconciled_note_en": "", "removed_segments": []}
