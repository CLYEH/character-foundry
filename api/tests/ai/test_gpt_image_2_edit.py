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


# --------------------------------------------------------------------------- alpha convention
#
# Regression: InpaintCanvas (web/src/components/aliases/InpaintCanvas.tsx)
# exports masks where the user's painted region is OPAQUE (alpha=255) and
# the untouched canvas is TRANSPARENT (alpha=0) — the natural Konva
# `layer.toCanvas` output.  OpenAI's `/v1/images/edits` contract is the
# opposite: alpha=0 marks the edit region; alpha=255 means preserve.
# Without an inversion the user's "edit here" stroke is read by OpenAI as
# "preserve here", and the rest of the canvas (mostly transparent) is
# treated as the edit region — the model regenerates almost the entire
# image while keeping only the small painted region intact, exactly
# opposite from user intent.


def _extract_multipart_part(body: bytes, content_type: str, name: str) -> bytes:
    """Return the binary content of a named part in a multipart body.

    The httpx client uses standard RFC 7578 multipart so a boundary split
    is enough — no need to pull in `python-multipart` for this one test.
    """
    boundary = content_type.split("boundary=", 1)[1].encode("latin-1")
    sep = b"--" + boundary
    for part in body.split(sep):
        marker = f'name="{name}"'.encode("latin-1")
        if marker not in part:
            continue
        head_end = part.index(b"\r\n\r\n") + 4
        # Strip trailing \r\n that precedes the next boundary.
        return part[head_end:].rstrip(b"\r\n")
    raise AssertionError(f"multipart part name={name!r} not found")


async def test_edit_inpaint_inverts_frontend_alpha_to_openai_convention(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """The wire mask must use OpenAI's convention (alpha=0 = edit) even
    when the caller hands in a mask in the frontend's natural convention
    (painted region = alpha=255). See header comment for why."""
    base = _make_rgba_png(32, 32, alpha=255)

    # Frontend-style mask: 32×32 transparent canvas with a small opaque
    # 8×8 "stroke" at the center — the shape Konva's `layer.toCanvas`
    # produces after the user paints once.
    mask_im = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    for y in range(12, 20):
        for x in range(12, 20):
            mask_im.putpixel((x, y), (255, 255, 255, 255))
    buf = io.BytesIO()
    mask_im.save(buf, format="PNG")
    frontend_mask = buf.getvalue()

    captured: dict[str, bytes | str] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        captured["content_type"] = request.headers.get("content-type", "")
        return _success_response(request)

    client = _make_client(fake_redis, _handler)
    try:
        await client.edit_inpaint(
            base_image_bytes=base,
            mask_png_bytes=frontend_mask,
            prompt="x",
        )
    finally:
        await client.aclose()

    sent_mask_bytes = _extract_multipart_part(
        captured["body"],  # type: ignore[arg-type]
        captured["content_type"],  # type: ignore[arg-type]
        "mask",
    )
    sent_mask = Image.open(io.BytesIO(sent_mask_bytes)).convert("RGBA")
    alpha = sent_mask.getchannel("A")

    painted_alpha = alpha.getpixel((16, 16))
    unpainted_alpha = alpha.getpixel((0, 0))
    assert painted_alpha == 0, (
        f"painted region (user 'edit here') must be alpha=0 on the wire "
        f"(OpenAI 'edit' convention); got alpha={painted_alpha}. The mask "
        f"is being shipped to OpenAI in the frontend's convention without "
        f"inversion — see header comment in this test section."
    )
    assert unpainted_alpha == 255, (
        f"unpainted region must be alpha=255 on the wire (OpenAI "
        f"'preserve' convention); got alpha={unpainted_alpha}."
    )


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
