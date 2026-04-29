"""Veo31Client + VeoStub behaviour against a mocked Veo API (T-029)."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable

import fakeredis.aioredis
import httpx
import pytest

from app.ai.factory import get_video_client
from app.ai.stub import VeoStub
from app.ai.veo_3_1 import VEO_SERVICE_NAME, Veo31Client
from app.core.errors import AgentErrorException

_FAKE_MP4 = b"\x00\x00\x00 ftypisom\x00\x00\x02\x00isomiso2avc1mp41" + b"\x00" * 32
_FAKE_MP4_B64 = base64.b64encode(_FAKE_MP4).decode("ascii")

_API_BASE = "https://veo.test/v1beta"
_OPERATION_NAME = "models/veo-3.1/operations/abc123"


def _make_client(
    fake_redis: fakeredis.aioredis.FakeRedis,
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    max_retries: int = 2,
    poll_interval_seconds: float = 0.0,
    max_poll_attempts: int = 5,
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> Veo31Client:
    """Build a Veo31Client wired to an httpx MockTransport.

    `monkeypatch.setattr(asyncio, "sleep", ...)` keeps retry / poll loops
    instant without depending on real timers.
    """
    if monkeypatch is not None:

        async def _no_sleep(_seconds: float) -> None:
            return None

        monkeypatch.setattr("asyncio.sleep", _no_sleep)

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        transport=transport,
        headers={"x-goog-api-key": "test-key"},
    )
    return Veo31Client(
        redis=fake_redis,
        api_key="test-key",
        api_url=_API_BASE,
        model="veo-3.1",
        timeout_seconds=2.0,
        max_retries=max_retries,
        poll_interval_seconds=poll_interval_seconds,
        max_poll_attempts=max_poll_attempts,
        http_client=http_client,
    )


def _submit_response(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"name": _OPERATION_NAME})


def _done_response_inline(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "name": _OPERATION_NAME,
            "done": True,
            "response": {
                "videos": [{"bytesBase64Encoded": _FAKE_MP4_B64, "durationSeconds": 5}],
            },
            "metadata": {"model": "veo-3.1-preview"},
        },
    )


def _make_seq_handler(
    submit_fn: Callable[[httpx.Request], httpx.Response] = _submit_response,
    poll_fns: list[Callable[[httpx.Request], httpx.Response]] | None = None,
) -> tuple[Callable[[httpx.Request], httpx.Response], dict[str, int]]:
    """Build a handler that routes by URL: POSTs go to `submit_fn`, GETs cycle
    through `poll_fns`. Returns the handler plus a counter dict so tests can
    assert call sequencing.
    """
    counters = {"submit": 0, "poll": 0}
    polls = list(poll_fns or [_done_response_inline])

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            counters["submit"] += 1
            return submit_fn(request)
        index = min(counters["poll"], len(polls) - 1)
        counters["poll"] += 1
        return polls[index](request)

    return _handler, counters


# ---------------------------------------------------------------------------
# Stub behaviour
# ---------------------------------------------------------------------------


async def test_stub_returns_valid_mp4_bytes() -> None:
    stub = VeoStub()
    result = await stub.generate_i2v(image_bytes=b"img", prompt="wave hello")

    # `ftyp` magic at offset 4 — same check every mp4 magic detector applies.
    assert result.video_bytes[4:8] == b"ftyp"
    assert result.video_bytes is stub.video_bytes
    assert result.model_version == VeoStub.MODEL_VERSION
    assert result.duration_ms == VeoStub.FIXTURE_DURATION_MS
    assert result.generation_log_payload["stub"] is True


async def test_stub_echoes_caller_duration_seconds() -> None:
    """The fixture is a fixed-length placeholder, but worker code reads
    `duration_ms` to display playback length; echo the requested value so
    the GenerationLog matches what was asked for."""
    stub = VeoStub()
    result = await stub.generate_i2v(image_bytes=b"img", prompt="wave", duration_seconds=3.5)
    assert result.duration_ms == 3500


async def test_stub_returns_same_bytes_across_calls() -> None:
    stub = VeoStub()
    a = await stub.generate_i2v(image_bytes=b"a", prompt="x")
    b = await stub.generate_i2v(image_bytes=b"b", prompt="y")
    assert a.video_bytes == b.video_bytes


# ---------------------------------------------------------------------------
# Real client — happy paths
# ---------------------------------------------------------------------------


async def test_real_client_submit_poll_inline_download_succeeds(
    fake_redis: fakeredis.aioredis.FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    handler, counters = _make_seq_handler(
        poll_fns=[
            # First poll: still running.
            lambda _r: httpx.Response(200, json={"name": _OPERATION_NAME, "done": False}),
            _done_response_inline,
        ]
    )
    client = _make_client(fake_redis, handler, monkeypatch=monkeypatch)
    try:
        result = await client.generate_i2v(
            image_bytes=b"parent-png", prompt="wave hello", duration_seconds=5
        )
    finally:
        await client.aclose()

    assert result.video_bytes == _FAKE_MP4
    assert result.duration_ms == 5000
    assert result.model_version == "veo-3.1-preview"  # picked from operation.metadata.model
    assert result.generation_log_payload["operation_name"] == _OPERATION_NAME
    # The redacted operation envelope must NOT include the base64 video payload.
    redacted = result.generation_log_payload["operation"]
    redacted_video = redacted["response"]["videos"][0]
    assert "bytesBase64Encoded" not in redacted_video
    assert counters["submit"] == 1
    assert counters["poll"] == 2


async def test_real_client_submit_sends_first_and_last_frame(
    fake_redis: fakeredis.aioredis.FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Identity-preservation contract (DECISIONS §3): the parent image must
    be sent as BOTH first frame and last frame."""
    captured: dict[str, object] = {}

    def _submit(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return _submit_response(request)

    handler, _ = _make_seq_handler(submit_fn=_submit, poll_fns=[_done_response_inline])
    client = _make_client(fake_redis, handler, monkeypatch=monkeypatch)
    try:
        await client.generate_i2v(image_bytes=b"parent-png", prompt="hi")
    finally:
        await client.aclose()

    body = captured["body"]
    assert isinstance(body, dict)
    instance = body["instances"][0]
    assert instance["image"]["bytesBase64Encoded"] == base64.b64encode(b"parent-png").decode()
    assert instance["lastFrame"] == instance["image"]


async def test_submit_body_does_not_send_personGeneration(
    fake_redis: fakeredis.aioredis.FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex P1 round-4: hardcoding `personGeneration: allow_all` is rejected
    for Veo i2v and explicitly forbidden in EU/UK/CH/MENA where only
    `allow_adult` is allowed. Phase 1 omits the parameter entirely so Veo
    applies its own server-side default; per-region configurability is
    deferred until we have a real deployment to tune for.
    """
    captured: dict[str, object] = {}

    def _submit(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return _submit_response(request)

    handler, _ = _make_seq_handler(submit_fn=_submit, poll_fns=[_done_response_inline])
    client = _make_client(fake_redis, handler, monkeypatch=monkeypatch)
    try:
        await client.generate_i2v(image_bytes=b"i", prompt="hi")
    finally:
        await client.aclose()

    body = captured["body"]
    assert isinstance(body, dict)
    parameters = body.get("parameters", {})
    assert "personGeneration" not in parameters, (
        "personGeneration must not be hardcoded — Veo i2v rejects allow_all and "
        "regional rules vary; let the server default apply."
    )


async def test_real_client_supports_videoUri_download(
    fake_redis: fakeredis.aioredis.FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    download_url = "https://veo.test/blobs/abc.mp4"
    download_request_headers: dict[str, str] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return _submit_response(request)
        if str(request.url) == download_url:
            download_request_headers.update(dict(request.headers))
            return httpx.Response(200, content=_FAKE_MP4)
        return httpx.Response(
            200,
            json={
                "name": _OPERATION_NAME,
                "done": True,
                "response": {"videos": [{"videoUri": download_url}]},
            },
        )

    client = _make_client(fake_redis, _handler, monkeypatch=monkeypatch)
    try:
        result = await client.generate_i2v(image_bytes=b"img", prompt="x", duration_seconds=4)
    finally:
        await client.aclose()

    assert result.video_bytes == _FAKE_MP4
    assert result.duration_ms == 4000
    # Codex P1 round-3: the videoUri GET must carry the provider API key
    # because the documented Gemini Veo flow uses Gemini API auth on it
    # (the URI is not an arbitrary signed CDN URL).
    assert download_request_headers.get("x-goog-api-key") == "test-key"


async def test_videoUri_download_follows_redirect(
    fake_redis: fakeredis.aioredis.FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex P1 round-3: Veo's videoUri may 302-redirect to the actual media
    location. The client must follow the redirect rather than surface a 302
    as MODEL_INVALID_REQUEST."""
    initial_url = "https://veo.test/v1beta/operations/abc/media"
    final_url = "https://veo.test/blobs/abc.mp4"

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return _submit_response(request)
        if str(request.url) == initial_url:
            return httpx.Response(302, headers={"Location": final_url})
        if str(request.url) == final_url:
            return httpx.Response(200, content=_FAKE_MP4)
        return httpx.Response(
            200,
            json={
                "name": _OPERATION_NAME,
                "done": True,
                "response": {"videos": [{"videoUri": initial_url}]},
            },
        )

    client = _make_client(fake_redis, _handler, monkeypatch=monkeypatch)
    try:
        result = await client.generate_i2v(image_bytes=b"img", prompt="x", duration_seconds=2)
    finally:
        await client.aclose()

    assert result.video_bytes == _FAKE_MP4


# ---------------------------------------------------------------------------
# Real client — resilience paths
# ---------------------------------------------------------------------------


async def test_submit_timeout_retries_and_succeeds(
    fake_redis: fakeredis.aioredis.FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Timeouts during submission are transient — the outer retry envelope
    should restart the whole submit→poll→download flow."""
    submits = {"n": 0}

    def _submit(request: httpx.Request) -> httpx.Response:
        submits["n"] += 1
        if submits["n"] == 1:
            raise httpx.ReadTimeout("slow", request=request)
        return _submit_response(request)

    handler, _ = _make_seq_handler(submit_fn=_submit, poll_fns=[_done_response_inline])
    client = _make_client(fake_redis, handler, max_retries=2, monkeypatch=monkeypatch)
    try:
        result = await client.generate_i2v(image_bytes=b"i", prompt="hi", duration_seconds=3)
    finally:
        await client.aclose()

    assert result.video_bytes == _FAKE_MP4
    assert submits["n"] == 2
    # No degraded entry — eventual success cleared in-flight failure state.
    assert await fake_redis.get(f"degraded:{VEO_SERVICE_NAME}") is None


async def test_invalid_argument_does_not_retry_or_open_breaker(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """Operation-level INVALID_ARGUMENT (planning §4.4) is a client-side
    bug, not a transient health signal — must surface as MODEL_INVALID_REQUEST
    without retry and without breaker pressure."""
    submits = {"n": 0}

    def _submit(request: httpx.Request) -> httpx.Response:
        submits["n"] += 1
        return _submit_response(request)

    poll_invalid = lambda _r: httpx.Response(  # noqa: E731
        200,
        json={
            "name": _OPERATION_NAME,
            "done": True,
            "error": {"status": "INVALID_ARGUMENT", "message": "bad prompt"},
        },
    )
    handler, _ = _make_seq_handler(submit_fn=_submit, poll_fns=[poll_invalid])
    client = _make_client(fake_redis, handler, max_retries=2)
    try:
        with pytest.raises(AgentErrorException) as info:
            await client.generate_i2v(image_bytes=b"i", prompt="hi")
    finally:
        await client.aclose()

    assert info.value.error.code == "MODEL_INVALID_REQUEST"
    assert submits["n"] == 1, "non-retryable error must not retry"
    assert await fake_redis.get(f"degraded:{VEO_SERVICE_NAME}") is None
    assert await fake_redis.zcard(f"circuit:{VEO_SERVICE_NAME}:failures") == 0


async def test_resource_exhausted_maps_to_quota_exceeded(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    poll_quota = lambda _r: httpx.Response(  # noqa: E731
        200,
        json={
            "name": _OPERATION_NAME,
            "done": True,
            "error": {"status": "RESOURCE_EXHAUSTED", "message": "quota out"},
        },
    )
    handler, _ = _make_seq_handler(poll_fns=[poll_quota])
    client = _make_client(fake_redis, handler, max_retries=0)
    try:
        with pytest.raises(AgentErrorException) as info:
            await client.generate_i2v(image_bytes=b"i", prompt="hi")
    finally:
        await client.aclose()

    assert info.value.error.code == "MODEL_QUOTA_EXCEEDED"
    assert info.value.error.retryable is False
    # Non-retryable → must NOT count toward breaker (see gpt-image-2 contract).
    assert await fake_redis.zcard(f"circuit:{VEO_SERVICE_NAME}:failures") == 0


async def test_5xx_during_submit_opens_breaker_after_threshold(
    fake_redis: fakeredis.aioredis.FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Five failed calls (each a single breaker failure) → breaker OPEN →
    sixth call short-circuits with MODEL_UNAVAILABLE without making any
    HTTP requests, and `degraded:veo-3.1` is set in Redis."""
    calls = {"n": 0}

    def _handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, json={"error": {"message": "down"}})

    client = _make_client(fake_redis, _handler, max_retries=0, monkeypatch=monkeypatch)
    try:
        for _ in range(5):
            with pytest.raises(AgentErrorException):
                await client.generate_i2v(image_bytes=b"i", prompt="x")

        before = calls["n"]
        with pytest.raises(AgentErrorException) as info:
            await client.generate_i2v(image_bytes=b"i", prompt="x")
        assert info.value.error.code == "MODEL_UNAVAILABLE"
        assert calls["n"] == before, "circuit OPEN must not call upstream"
    finally:
        await client.aclose()

    raw = await fake_redis.get(f"degraded:{VEO_SERVICE_NAME}")
    assert raw is not None
    payload = json.loads(raw)
    assert payload["reason"] == "CIRCUIT_OPEN"


async def test_poll_loop_exhausts_max_attempts_with_model_timeout(
    fake_redis: fakeredis.aioredis.FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stuck operation that never flips `done` must surface as
    MODEL_TIMEOUT once the poll budget is exhausted (don't poll forever)."""
    in_progress = lambda _r: httpx.Response(  # noqa: E731
        200, json={"name": _OPERATION_NAME, "done": False}
    )
    handler, _ = _make_seq_handler(poll_fns=[in_progress])
    client = _make_client(
        fake_redis,
        handler,
        max_retries=0,
        max_poll_attempts=3,
        monkeypatch=monkeypatch,
    )
    try:
        with pytest.raises(AgentErrorException) as info:
            await client.generate_i2v(image_bytes=b"i", prompt="x")
    finally:
        await client.aclose()

    assert info.value.error.code == "MODEL_TIMEOUT"


async def test_duration_seconds_clamps_above_max_before_submit(
    fake_redis: fakeredis.aioredis.FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex P1 round-6 + planning §4.3: out-of-range durations must be
    clamped to the supported band (1.0–10.0s in Phase 1) BEFORE the
    submit body is built. Veo otherwise rejects with a non-retryable
    INVALID_ARGUMENT, turning a recoverable user mistake into a hard
    failure."""
    captured: dict[str, object] = {}

    def _submit(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return _submit_response(request)

    handler, _ = _make_seq_handler(submit_fn=_submit, poll_fns=[_done_response_inline])
    client = _make_client(fake_redis, handler, monkeypatch=monkeypatch)
    try:
        result = await client.generate_i2v(image_bytes=b"i", prompt="x", duration_seconds=99.0)
    finally:
        await client.aclose()

    body = captured["body"]
    assert isinstance(body, dict)
    assert body["parameters"]["durationSeconds"] == 10.0  # clamped to max
    # The result's duration_ms must reflect the clamped value, not the
    # caller's raw request — otherwise GenerationLog would record a value
    # that doesn't match the actual rendered video length.
    assert result.duration_ms == 10000


async def test_duration_seconds_clamps_below_min_before_submit(
    fake_redis: fakeredis.aioredis.FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Counterpart to the above: durations below the minimum (e.g. 0.1s)
    are clamped UP to the floor."""
    captured: dict[str, object] = {}

    def _submit(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return _submit_response(request)

    handler, _ = _make_seq_handler(submit_fn=_submit, poll_fns=[_done_response_inline])
    client = _make_client(fake_redis, handler, monkeypatch=monkeypatch)
    try:
        result = await client.generate_i2v(image_bytes=b"i", prompt="x", duration_seconds=0.1)
    finally:
        await client.aclose()

    body = captured["body"]
    assert body["parameters"]["durationSeconds"] == 1.0  # clamped to min
    assert result.duration_ms == 1000


async def test_duration_seconds_in_range_passes_through_unchanged(
    fake_redis: fakeredis.aioredis.FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase 1 preset durations (3-5s) sit comfortably inside the band and
    must pass through without modification."""
    captured: dict[str, object] = {}

    def _submit(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return _submit_response(request)

    handler, _ = _make_seq_handler(submit_fn=_submit, poll_fns=[_done_response_inline])
    client = _make_client(fake_redis, handler, monkeypatch=monkeypatch)
    try:
        result = await client.generate_i2v(image_bytes=b"i", prompt="x", duration_seconds=3.5)
    finally:
        await client.aclose()

    body = captured["body"]
    assert body["parameters"]["durationSeconds"] == 3.5
    assert result.duration_ms == 3500


async def test_caller_nan_duration_does_not_raise(
    fake_redis: fakeredis.aioredis.FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex P2 round-7: `int(NaN * 1000)` raises ValueError, bypassing the
    AgentError mapping. A NaN from the caller must collapse to a finite
    value before it reaches the duration_ms conversion. (Same rule for
    Infinity, covered separately.)"""
    handler, _ = _make_seq_handler(poll_fns=[_done_response_inline])
    client = _make_client(fake_redis, handler, monkeypatch=monkeypatch)
    try:
        result = await client.generate_i2v(
            image_bytes=b"i", prompt="x", duration_seconds=float("nan")
        )
    finally:
        await client.aclose()

    # NaN clamps to the minimum (1.0s) → 1000ms.
    assert result.duration_ms == 1000


async def test_caller_inf_duration_does_not_raise(
    fake_redis: fakeredis.aioredis.FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Counterpart to NaN: Infinity also breaks `int(...)` with
    OverflowError unless guarded."""
    handler, _ = _make_seq_handler(poll_fns=[_done_response_inline])
    client = _make_client(fake_redis, handler, monkeypatch=monkeypatch)
    try:
        result = await client.generate_i2v(
            image_bytes=b"i", prompt="x", duration_seconds=float("inf")
        )
    finally:
        await client.aclose()

    assert result.duration_ms == 1000


async def test_response_nan_duration_falls_back_to_zero(
    fake_redis: fakeredis.aioredis.FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A malformed provider response carrying NaN in `durationSeconds` must
    not propagate to `int(NaN * 1000)`. `_video_item_from` drops non-finite
    values during normalisation, so the fallback is `_extract_duration_seconds`
    returning None → duration_ms = 0.

    NaN is non-standard JSON so we hand-write the body — httpx's `json=`
    encoder refuses to emit it. Python's stdlib `json.loads` reads `NaN`
    back as `float('nan')`, which is exactly the lenient-parser shape this
    guard is meant to defend against.
    """
    nan_body = (
        b'{"name": "'
        + _OPERATION_NAME.encode()
        + b'", "done": true, "response": {"videos": [{"bytesBase64Encoded": "'
        + _FAKE_MP4_B64.encode()
        + b'", "durationSeconds": NaN}]}}'
    )
    poll_done_with_nan = lambda _r: httpx.Response(  # noqa: E731
        200,
        headers={"content-type": "application/json"},
        content=nan_body,
    )
    handler, _ = _make_seq_handler(poll_fns=[poll_done_with_nan])
    client = _make_client(fake_redis, handler, monkeypatch=monkeypatch)
    try:
        # Caller doesn't supply duration → falls back to response value.
        result = await client.generate_i2v(image_bytes=b"i", prompt="x")
    finally:
        await client.aclose()

    # NaN was dropped → fallback to 0 (no value available anywhere).
    assert result.duration_ms == 0


async def test_stub_clamps_duration_seconds_for_consistency() -> None:
    """The stub must apply the same clamp as the real client so callers
    see consistent duration_ms regardless of which mode is wired in
    (Codex P1 round-6)."""
    stub = VeoStub()
    high = await stub.generate_i2v(image_bytes=b"i", prompt="x", duration_seconds=99.0)
    assert high.duration_ms == 10000
    low = await stub.generate_i2v(image_bytes=b"i", prompt="x", duration_seconds=0.1)
    assert low.duration_ms == 1000


async def test_auth_failure_remediation_names_VEO_API_KEY(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """Codex P2 round-5: Veo's auth-failure remediation must name
    `VEO_API_KEY`, not the default `OPENAI_API_KEY` — operators rotating
    the wrong credential delays incident triage."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    client = _make_client(fake_redis, _handler, max_retries=0)
    try:
        with pytest.raises(AgentErrorException) as info:
            await client.generate_i2v(image_bytes=b"i", prompt="x")
    finally:
        await client.aclose()

    err = info.value.error
    assert err.code == "INTERNAL_AUTH_FAILED"
    assert "VEO_API_KEY" in err.cause
    assert "VEO_API_KEY" in err.fix
    assert "OPENAI_API_KEY" not in err.cause
    assert "OPENAI_API_KEY" not in err.fix


async def test_redaction_strips_inline_bytes_from_generatedSamples(
    fake_redis: fakeredis.aioredis.FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex P2 round-5: when Veo returns inline bytes under
    `response.generateVideoResponse.generatedSamples[].video.bytesBase64-
    Encoded`, the redacted payload in `generation_log_payload['operation']`
    must drop the bytes — otherwise multi-MB base64 lands in audit storage.
    """
    huge_b64 = "A" * 10_000  # stand-in for a real inline-bytes payload

    poll_done = lambda _r: httpx.Response(  # noqa: E731
        200,
        json={
            "name": _OPERATION_NAME,
            "done": True,
            "response": {
                "generateVideoResponse": {
                    "generatedSamples": [
                        {
                            "video": {
                                "bytesBase64Encoded": huge_b64,
                                "durationSeconds": 4,
                            }
                        }
                    ]
                }
            },
        },
    )
    handler, _ = _make_seq_handler(poll_fns=[poll_done])
    client = _make_client(fake_redis, handler, monkeypatch=monkeypatch)
    try:
        result = await client.generate_i2v(image_bytes=b"i", prompt="x")
    finally:
        await client.aclose()

    redacted = result.generation_log_payload["operation"]
    # Walk down to the inner `video` dict and assert the bytes key was scrubbed.
    inner_video = redacted["response"]["generateVideoResponse"]["generatedSamples"][0]["video"]
    assert "bytesBase64Encoded" not in inner_video
    # Non-bytes metadata should still be there.
    assert inner_video["durationSeconds"] == 4
    # Sanity: the giant string must not appear anywhere in the redacted blob.
    assert huge_b64 not in json.dumps(redacted)


async def test_real_client_supports_generatedSamples_response_shape(
    fake_redis: fakeredis.aioredis.FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex P1 round-4: current Google Veo REST examples return the
    `response.generateVideoResponse.generatedSamples[].video` shape with
    `uri` (not `videoUri`) on the inner object. The client must
    normalise this shape into the same canonical pipeline as
    `response.videos[]` so a successful operation is downloaded properly.
    """
    download_url = "https://veo.test/blobs/sample.mp4"

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return _submit_response(request)
        if str(request.url) == download_url:
            return httpx.Response(200, content=_FAKE_MP4)
        return httpx.Response(
            200,
            json={
                "name": _OPERATION_NAME,
                "done": True,
                "response": {
                    "generateVideoResponse": {
                        "generatedSamples": [
                            {"video": {"uri": download_url, "durationSeconds": 6}}
                        ],
                    }
                },
            },
        )

    client = _make_client(fake_redis, _handler, monkeypatch=monkeypatch)
    try:
        result = await client.generate_i2v(image_bytes=b"img", prompt="x")
    finally:
        await client.aclose()

    assert result.video_bytes == _FAKE_MP4
    # When duration_seconds isn't supplied by the caller, fall back to the
    # operation's reported duration — which lives at the inner `video.durationSeconds`
    # path under generatedSamples.
    assert result.duration_ms == 6000


async def test_truly_empty_response_raises_invalid_request(
    fake_redis: fakeredis.aioredis.FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A done operation with no recognised video shape (neither
    `response.videos[]` nor `generateVideoResponse.generatedSamples`)
    surfaces as MODEL_INVALID_REQUEST so callers see a loud error rather
    than silently succeeding with no bytes.
    """
    poll_empty = lambda _r: httpx.Response(  # noqa: E731
        200,
        json={
            "name": _OPERATION_NAME,
            "done": True,
            "response": {"unrelatedField": "no videos here"},
        },
    )
    handler, _ = _make_seq_handler(poll_fns=[poll_empty])
    client = _make_client(fake_redis, handler, max_retries=0, monkeypatch=monkeypatch)
    try:
        with pytest.raises(AgentErrorException) as info:
            await client.generate_i2v(image_bytes=b"i", prompt="x")
    finally:
        await client.aclose()

    assert info.value.error.code == "MODEL_INVALID_REQUEST"


async def test_videoUri_5xx_is_model_unavailable_and_feeds_breaker(
    fake_redis: fakeredis.aioredis.FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 5xx from the videoUri endpoint signals upstream sickness and
    SHOULD feed the breaker so /v1/meta can surface degradation. The
    standard provider-error mapper (shared with submit / poll) maps 5xx to
    MODEL_UNAVAILABLE (retryable signal)."""
    download_url = "https://veo.test/blobs/transient.mp4"

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return _submit_response(request)
        if str(request.url) == download_url:
            return httpx.Response(503, content=b"upstream down")
        return httpx.Response(
            200,
            json={
                "name": _OPERATION_NAME,
                "done": True,
                "response": {"videos": [{"videoUri": download_url}]},
            },
        )

    client = _make_client(fake_redis, _handler, max_retries=0, monkeypatch=monkeypatch)
    try:
        with pytest.raises(AgentErrorException) as info:
            await client.generate_i2v(image_bytes=b"i", prompt="x")
    finally:
        await client.aclose()

    assert info.value.error.code == "MODEL_UNAVAILABLE"
    # Retryable upstream signal feeds breaker accounting.
    assert await fake_redis.zcard(f"circuit:{VEO_SERVICE_NAME}:failures") == 1


async def test_stub_fixture_is_packaged_via_importlib_resources() -> None:
    """Codex P1 round-1: the mp4 fixture must be packaged with the wheel
    so non-editable installs (Docker image's `pip install .`) don't crash
    on the first stub call. `pyproject.toml` `[tool.setuptools.package-data]`
    must include `*.mp4` for `app.ai._fixtures`. This test exercises the
    importlib.resources path the stub uses, so a packaging regression
    surfaces here instead of in production.
    """
    from importlib import resources

    package = resources.files("app.ai._fixtures")
    fixture = package.joinpath("veo_placeholder.mp4")
    assert fixture.is_file(), (
        "veo_placeholder.mp4 must be reachable via importlib.resources; "
        "if this fails, add `*.mp4` to [tool.setuptools.package-data]."
    )
    data = fixture.read_bytes()
    assert data[4:8] == b"ftyp", "fixture should be a valid mp4 (ftyp box at offset 4)"


async def test_post_submit_poll_5xx_does_not_resubmit(
    fake_redis: fakeredis.aioredis.FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once Veo accepts the long-running operation we've already paid for
    a generation; a poll-time 5xx must NOT trigger a fresh `predictLong-
    Running` POST (planning §4.4: "影片重試很貴"). Failure should propagate
    and feed the breaker once.
    """
    poll_5xx = lambda _r: httpx.Response(503, json={"error": {"message": "down"}})  # noqa: E731
    handler, counters = _make_seq_handler(poll_fns=[poll_5xx])
    client = _make_client(fake_redis, handler, max_retries=2, monkeypatch=monkeypatch)
    try:
        with pytest.raises(AgentErrorException) as info:
            await client.generate_i2v(image_bytes=b"i", prompt="x")
    finally:
        await client.aclose()

    assert info.value.error.code == "MODEL_UNAVAILABLE"
    assert counters["submit"] == 1, "post-submit failure must not trigger resubmission"
    # Single failure recorded toward the breaker (mirrors per-call accounting).
    assert await fake_redis.zcard(f"circuit:{VEO_SERVICE_NAME}:failures") == 1


async def test_submit_400_does_not_retry_or_open_breaker(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """A 400 from submission is a client-side payload bug; must not retry
    and must not push the breaker toward OPEN."""
    calls = {"n": 0}

    def _handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, json={"error": {"message": "bad payload"}})

    client = _make_client(fake_redis, _handler, max_retries=2)
    try:
        with pytest.raises(AgentErrorException) as info:
            await client.generate_i2v(image_bytes=b"i", prompt="x")
    finally:
        await client.aclose()

    assert info.value.error.code == "MODEL_INVALID_REQUEST"
    assert calls["n"] == 1
    assert await fake_redis.zcard(f"circuit:{VEO_SERVICE_NAME}:failures") == 0


# ---------------------------------------------------------------------------
# Factory wiring
# ---------------------------------------------------------------------------


def test_factory_returns_stub_when_AI_STUB_MODE_true(
    fake_redis: fakeredis.aioredis.FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_STUB_MODE", "true")
    client = get_video_client(fake_redis)
    assert isinstance(client, VeoStub)


def test_factory_returns_real_client_when_AI_STUB_MODE_false(
    fake_redis: fakeredis.aioredis.FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_STUB_MODE", "false")
    monkeypatch.setenv("VEO_API_KEY", "test-key")
    client = get_video_client(fake_redis)
    assert isinstance(client, Veo31Client)


def test_factory_default_is_stub_mode(
    fake_redis: fakeredis.aioredis.FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 1 dev/CI default — never charge the Veo account by accident."""
    monkeypatch.delenv("AI_STUB_MODE", raising=False)
    client = get_video_client(fake_redis)
    assert isinstance(client, VeoStub)
