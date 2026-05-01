"""GptImage2Client edit-mode behaviour against a mocked OpenAI Images API (T-030).

Covers the two new methods used by Sprint 3 alias generation:

  - `edit_image2image` — base + 0..N reference images (multipart with
    repeated `image` field name)
  - `edit_inpaint` — base + alpha-mask PNG, with client-side validation
    of mask dimensions and emptiness before any provider call

The text2image / image2image / inpaint resilience surface is exercised
in `test_gpt_image_2.py`; here we focus on the edit-specific behaviour
and re-verify the breaker on the new code paths to catch any regression
in the shared `_call_with_resilience` plumbing.
"""

from __future__ import annotations

import base64
import io
from collections.abc import Callable

import fakeredis.aioredis
import httpx
import pytest
from PIL import Image

from app.ai.gpt_image_2 import GptImage2Client
from app.core.errors import AgentErrorException

_FAKE_PNG = b"\x89PNG\r\n\x1a\nstub-bytes"
_FAKE_PNG_B64 = base64.b64encode(_FAKE_PNG).decode("ascii")


def _make_rgba_png(width: int, height: int, *, alpha: int) -> bytes:
    """Return a width×height RGBA PNG with uniform alpha.

    Uniform alpha lets each test express "fully transparent mask" /
    "fully opaque mask" without composing pixel arrays. PIL is already
    a project dependency (`api/app/utils/thumbnails.py`).
    """
    img = Image.new("RGBA", (width, height), (0, 0, 0, alpha))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_client(
    fake_redis: fakeredis.aioredis.FakeRedis,
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    max_retries: int = 3,
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> GptImage2Client:
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


# --------------------------------------------------------------------------- happy


async def test_edit_image2image_with_three_references_sends_each_image_field(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """Multi-image edits use the `image[]` array syntax. Repeating the
    bare `image` field name (the original gpt-image-1 assumption) returns
    400 on gpt-image-1.5+; the provider's own error message instructs us
    to use `image[]`. Verified empirically against real provider on T-042."""
    captured: dict[str, str] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["content_type"] = request.headers.get("content-type", "")
        captured["body"] = request.content.decode("latin-1")
        return _success_response(request)

    base = b"BASE-bytes-marker"
    refs = [b"REF-one-marker", b"REF-two-marker", b"REF-three-marker"]

    client = _make_client(fake_redis, _handler)
    try:
        result = await client.edit_image2image(
            base_image_bytes=base,
            reference_image_bytes=refs,
            prompt="add a red scarf",
        )
    finally:
        await client.aclose()

    assert result.image_bytes == _FAKE_PNG
    assert "multipart/form-data" in captured["content_type"]
    body = captured["body"]
    assert "BASE-bytes-marker" in body
    for ref in refs:
        assert ref.decode("latin-1") in body, f"reference {ref!r} missing from multipart body"
    # Base + each reference must all carry the `image[]` array field name
    # so the provider parses them as a single multi-image edit. The bare
    # `image` field MUST NOT appear (would re-introduce the 400 bug).
    assert body.count('name="image[]"') == 1 + len(refs)
    assert 'name="image"' not in body
    assert "add a red scarf" in body


async def test_edit_image2image_with_no_references_still_sends_base(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """Empty / None references is the `text` alias mode (T-031): just
    the base image + freeform prompt."""
    captured: dict[str, str] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode("latin-1")
        return _success_response(request)

    client = _make_client(fake_redis, _handler)
    try:
        result = await client.edit_image2image(
            base_image_bytes=b"only-base",
            reference_image_bytes=None,
            prompt="restyle",
        )
    finally:
        await client.aclose()

    assert result.image_bytes == _FAKE_PNG
    assert "only-base" in captured["body"]
    assert captured["body"].count('name="image"') == 1


async def test_edit_inpaint_happy_sends_image_and_mask(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    base = _make_rgba_png(64, 96, alpha=255)
    # Half-transparent so the mask is a non-empty edit region.
    mask = _make_rgba_png(64, 96, alpha=0)
    captured: dict[str, str] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode("latin-1")
        return _success_response(request)

    client = _make_client(fake_redis, _handler)
    try:
        result = await client.edit_inpaint(
            base_image_bytes=base,
            mask_png_bytes=mask,
            prompt="erase background",
        )
    finally:
        await client.aclose()

    assert result.image_bytes == _FAKE_PNG
    body = captured["body"]
    # Exactly one image + one mask; inpaint never repeats `image`.
    assert body.count('name="image"') == 1
    assert body.count('name="mask"') == 1
    assert "erase background" in body


# --------------------------------------------------------------------------- validation


async def test_edit_inpaint_rejects_mismatched_mask_size(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """Validation must run *before* any provider call — the handler
    should never fire."""
    calls = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return _success_response(request)

    base = _make_rgba_png(64, 96, alpha=255)
    mask = _make_rgba_png(32, 48, alpha=0)  # wrong dims

    client = _make_client(fake_redis, _handler)
    try:
        with pytest.raises(AgentErrorException) as info:
            await client.edit_inpaint(base_image_bytes=base, mask_png_bytes=mask, prompt="x")
    finally:
        await client.aclose()

    assert info.value.error.code == "VALIDATION_MASK_SIZE_MISMATCH"
    assert calls["n"] == 0, "validation must short-circuit before HTTP call"
    # Failed validation must NOT count as a breaker failure (deterministic
    # user-input error, not an upstream availability signal).
    assert await fake_redis.zcard("circuit:gpt-image-2:failures") == 0


async def test_edit_inpaint_rejects_empty_mask(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """A fully-opaque mask (alpha=255 everywhere) carries no edit region
    under OpenAI's alpha-mask convention."""
    calls = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return _success_response(request)

    base = _make_rgba_png(64, 96, alpha=255)
    mask = _make_rgba_png(64, 96, alpha=255)  # nothing transparent → empty

    client = _make_client(fake_redis, _handler)
    try:
        with pytest.raises(AgentErrorException) as info:
            await client.edit_inpaint(base_image_bytes=base, mask_png_bytes=mask, prompt="x")
    finally:
        await client.aclose()

    assert info.value.error.code == "VALIDATION_MASK_EMPTY"
    assert calls["n"] == 0
    assert await fake_redis.zcard("circuit:gpt-image-2:failures") == 0


# --------------------------------------------------------------------------- resilience


async def test_edit_image2image_5xx_then_success_recovers(
    fake_redis: fakeredis.aioredis.FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retry / breaker plumbing is shared with text2image; sanity-check
    on the new edit code path so a future refactor can't silently bypass
    `_call_with_resilience` for edits."""
    calls = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, json={"error": {"message": "down"}})
        return _success_response(request)

    client = _make_client(fake_redis, _handler, monkeypatch=monkeypatch)
    try:
        result = await client.edit_image2image(
            base_image_bytes=b"base", reference_image_bytes=[], prompt="p"
        )
    finally:
        await client.aclose()

    assert result.image_bytes == _FAKE_PNG
    assert calls["n"] == 2
    assert await fake_redis.get("degraded:gpt-image-2") is None


async def test_edit_inpaint_timeout_records_single_breaker_failure(
    fake_redis: fakeredis.aioredis.FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    base = _make_rgba_png(64, 96, alpha=255)
    mask = _make_rgba_png(64, 96, alpha=0)

    client = _make_client(fake_redis, _handler, max_retries=2, monkeypatch=monkeypatch)
    try:
        with pytest.raises(AgentErrorException) as info:
            await client.edit_inpaint(base_image_bytes=base, mask_png_bytes=mask, prompt="x")
    finally:
        await client.aclose()

    assert info.value.error.code == "MODEL_TIMEOUT"
    # One failed call → one breaker failure regardless of retry count.
    assert await fake_redis.zcard("circuit:gpt-image-2:failures") == 1
