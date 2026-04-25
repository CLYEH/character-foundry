"""GptImage2Client behaviour against a mocked OpenAI Images API (T-014)."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable

import fakeredis.aioredis
import httpx
import pytest

from app.ai.gpt_image_2 import GptImage2Client
from app.core.errors import AgentErrorException

# Tiny PNG magic bytes — every test asserts the response carries something
# that *could* be decoded into a PNG, without being picky about real layout.
_FAKE_PNG = b"\x89PNG\r\n\x1a\nstub-bytes"
_FAKE_PNG_B64 = base64.b64encode(_FAKE_PNG).decode("ascii")


def _make_client(
    fake_redis: fakeredis.aioredis.FakeRedis,
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    max_retries: int = 3,
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> GptImage2Client:
    """Build a GptImage2Client wired to an httpx MockTransport.

    `monkeypatch.setattr(asyncio, "sleep", ...)` keeps the retry tests fast
    without depending on real backoff durations.
    """
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
    return GptImage2Client(
        redis=fake_redis,
        api_key="test-key",
        api_base="https://api.openai.test/v1",
        model="gpt-image-2",
        timeout_seconds=2.0,
        max_retries=max_retries,
        http_client=http_client,
    )


def _success_response(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "gpt-image-2",
            "data": [{"b64_json": _FAKE_PNG_B64}],
        },
    )


async def test_text2image_happy_path_returns_png_bytes(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    client = _make_client(fake_redis, _success_response)
    try:
        result = await client.generate_image_text2image("a smiling cat", aspect_ratio="1:1")
    finally:
        await client.aclose()

    assert result.image_bytes == _FAKE_PNG
    assert result.image_bytes.startswith(b"\x89PNG")
    assert result.model_version == "gpt-image-2"
    assert result.cost_units > 0
    assert result.duration_ms >= 0


async def test_image2image_sends_multipart_with_image_file(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    captured: dict[str, str] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["content_type"] = request.headers.get("content-type", "")
        captured["body"] = request.content.decode("latin-1")
        return _success_response(request)

    client = _make_client(fake_redis, _handler)
    try:
        result = await client.generate_image_image2image(
            "make it red", b"raw-png-bytes", aspect_ratio="2:3"
        )
    finally:
        await client.aclose()

    assert result.image_bytes == _FAKE_PNG
    assert "multipart/form-data" in captured["content_type"]
    assert "raw-png-bytes" in captured["body"]


async def test_inpaint_sends_image_and_mask(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    captured: dict[str, str] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode("latin-1")
        return _success_response(request)

    client = _make_client(fake_redis, _handler)
    try:
        result = await client.generate_image_inpaint("swap shirt", b"image-bytes", b"mask-bytes")
    finally:
        await client.aclose()

    assert result.image_bytes == _FAKE_PNG
    assert "image-bytes" in captured["body"]
    assert "mask-bytes" in captured["body"]


async def test_5xx_then_success_records_success_and_returns(
    fake_redis: fakeredis.aioredis.FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, json={"error": {"message": "upstream"}})
        return _success_response(request)

    client = _make_client(fake_redis, _handler, monkeypatch=monkeypatch)
    try:
        result = await client.generate_image_text2image("retry me")
    finally:
        await client.aclose()

    assert result.image_bytes == _FAKE_PNG
    assert calls["n"] == 2
    # No degraded entry written; success cleared any in-flight state.
    assert await fake_redis.get("degraded:gpt-image-2") is None


async def test_timeout_raises_model_timeout_after_retries_exhausted(
    fake_redis: fakeredis.aioredis.FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    client = _make_client(fake_redis, _handler, max_retries=2, monkeypatch=monkeypatch)
    try:
        with pytest.raises(AgentErrorException) as info:
            await client.generate_image_text2image("hello")
    finally:
        await client.aclose()

    assert info.value.error.code == "MODEL_TIMEOUT"
    # Single failed call should record exactly one breaker failure, not three.
    failures = await fake_redis.zcard("circuit:gpt-image-2:failures")
    assert failures == 1


async def test_content_policy_400_does_not_retry(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    calls = {"n": 0}

    def _handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            400,
            json={
                "error": {
                    "code": "content_policy_violation",
                    "message": "rejected by safety system",
                }
            },
        )

    client = _make_client(fake_redis, _handler)
    try:
        with pytest.raises(AgentErrorException) as info:
            await client.generate_image_text2image("forbidden")
    finally:
        await client.aclose()

    assert info.value.error.code == "PROMPT_CONTENT_POLICY"
    assert calls["n"] == 1


async def test_429_maps_to_rate_limit_and_retries(
    fake_redis: fakeredis.aioredis.FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={})
        return _success_response(request)

    client = _make_client(fake_redis, _handler, monkeypatch=monkeypatch)
    try:
        result = await client.generate_image_text2image("ok")
    finally:
        await client.aclose()

    assert calls["n"] == 2
    assert result.image_bytes == _FAKE_PNG


async def test_five_failed_calls_open_circuit_then_short_circuit(
    fake_redis: fakeredis.aioredis.FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}

    def _handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, json={"error": {"message": "down"}})

    client = _make_client(fake_redis, _handler, max_retries=0, monkeypatch=monkeypatch)
    try:
        for _ in range(5):
            with pytest.raises(AgentErrorException):
                await client.generate_image_text2image("a")

        # 6th call: circuit is OPEN, must short-circuit without HTTP calls.
        before = calls["n"]
        with pytest.raises(AgentErrorException) as info:
            await client.generate_image_text2image("a")
        assert info.value.error.code == "MODEL_UNAVAILABLE"
        assert calls["n"] == before, "circuit OPEN must not call upstream"
    finally:
        await client.aclose()

    raw = await fake_redis.get("degraded:gpt-image-2")
    assert raw is not None
    payload = json.loads(raw)
    assert payload["reason"] == "CIRCUIT_OPEN"


async def test_circuit_recovers_after_degraded_key_expires(
    fake_redis: fakeredis.aioredis.FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the OPEN TTL elapses (here we just delete the key to simulate
    that), the next call must hit the provider again and on success clear
    the failure history.
    """
    # Seed: 5 failures → OPEN
    calls = {"n": 0}

    def _flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] <= 5:
            return httpx.Response(503, json={"error": {"message": "down"}})
        return _success_response(request)

    client = _make_client(fake_redis, _flaky, max_retries=0, monkeypatch=monkeypatch)
    try:
        for _ in range(5):
            with pytest.raises(AgentErrorException):
                await client.generate_image_text2image("a")
        assert await fake_redis.get("degraded:gpt-image-2") is not None

        # Simulate retry_at elapsing.
        await fake_redis.delete("degraded:gpt-image-2")

        # Next call succeeds.
        result = await client.generate_image_text2image("a")
        assert result.image_bytes == _FAKE_PNG

        # Both the public degraded key and the internal failures set are cleared.
        assert await fake_redis.get("degraded:gpt-image-2") is None
        assert await fake_redis.zcard("circuit:gpt-image-2:failures") == 0
    finally:
        await client.aclose()
