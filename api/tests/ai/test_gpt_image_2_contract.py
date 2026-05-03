"""Outgoing-body contract tests for GptImage2Client (T-044).

Existing tests in `test_gpt_image_2.py` / `test_gpt_image_2_edit.py` rely
on `httpx.MockTransport` but only assert HTTP method + URL + a few body
substrings. Both bug classes T-042 fixed (dall-e-3 leftover params +
multi-image field-name) slipped past CI because nothing pinned the wire
shape against the OpenAI gpt-image schema.

This file pins each of the five client methods to the OpenAI gpt-image
contract:

    - generate_image_text2image  → POST /images/generations  (JSON)
    - generate_image_image2image → POST /images/edits        (multipart, single `image`)
    - generate_image_inpaint     → POST /images/edits        (multipart, `image` + `mask`)
    - edit_image2image           → POST /images/edits        (multipart, `image[]` array
                                                              when refs present, bare `image`
                                                              when not)
    - edit_inpaint               → POST /images/edits        (multipart, `image` + `mask`)

Regression guards: the wire body must NOT contain the dall-e-3 legacy
fields T-042 removed — `response_format`, `seed`, or `quality="hd"`.

Schema source: `openai/openai-python` —
`src/openai/types/image_generate_params.py` & `image_edit_params.py`. We
mirror the relevant constants here rather than importing the SDK at
runtime; the SDK is a dev tool with heavy transitive deps. Mirror drift
is the price of independence; the weekly real-API smoke (planning §9.2)
is the safety net.
"""

from __future__ import annotations

import base64
import io
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from email.parser import BytesParser
from email.policy import default

import fakeredis.aioredis
import httpx
from PIL import Image

from app.ai.gpt_image_2 import GptImage2Client

# --------------------------------------------------------------------- schema

# Mirror of `image_generate_params.py` (gpt-image accepted fields). Anything
# beyond this set in the wire body is a contract bug.
_ALLOWED_GENERATIONS_FIELDS: frozenset[str] = frozenset(
    {
        "model",
        "prompt",
        "size",
        "n",
        # Optional gpt-image params we may legitimately add later.
        "output_format",
        "output_compression",
        "quality",
        "background",
        "moderation",
        "partial_images",
        "stream",
        "user",
    }
)

# Mirror of `image_edit_params.py`. `image[]` is the multi-image array
# shape; bare `image` is the single-image shape — both legal, never
# together. `mask` is inpaint-only.
_ALLOWED_EDITS_FIELDS: frozenset[str] = frozenset(
    {
        "model",
        "prompt",
        "image",
        "image[]",
        "mask",
        "size",
        "n",
        "output_format",
        "output_compression",
        "quality",
        "background",
        "stream",
        "user",
    }
)

# T-042 regression guard. These were sent by the original (dall-e-3-shaped)
# client and 400 against gpt-image. Any wire body containing them is a bug.
_FORBIDDEN_FIELDS_ALWAYS: frozenset[str] = frozenset({"response_format", "seed"})

# `quality` enum on gpt-image: auto / low / medium / high. `hd` and
# `standard` are dall-e-3 legacy values; sending either returns 400.
_ALLOWED_QUALITY_VALUES: frozenset[str] = frozenset({"auto", "low", "medium", "high"})

# Allowed `size` enum on gpt-image. The dall-e-3 1792x1024 / 1024x1792
# slots return 400 on gpt-image; client must never send them.
_ALLOWED_SIZE_VALUES: frozenset[str] = frozenset({"auto", "1024x1024", "1024x1536", "1536x1024"})


# --------------------------------------------------------------------- harness

_FAKE_PNG = b"\x89PNG\r\n\x1a\nstub-bytes"
_FAKE_PNG_B64 = base64.b64encode(_FAKE_PNG).decode("ascii")


def _make_rgba_png(width: int, height: int, *, alpha: int) -> bytes:
    img = Image.new("RGBA", (width, height), (0, 0, 0, alpha))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _success(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={"model": "gpt-image-2", "data": [{"b64_json": _FAKE_PNG_B64}]},
    )


def _make_client(
    fake_redis: fakeredis.aioredis.FakeRedis,
    handler: Callable[[httpx.Request], httpx.Response],
) -> GptImage2Client:
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
        max_retries=0,
        http_client=http_client,
    )


@dataclass
class _Captured:
    url: str = ""
    content_type: str = ""
    body: bytes = field(default=b"")


def _capture(captured: _Captured) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.url = str(request.url)
        captured.content_type = request.headers.get("content-type", "")
        captured.body = bytes(request.content)
        return _success(request)

    return handler


def _parse_multipart(body: bytes, content_type: str) -> dict[str, list[bytes]]:
    """Parse multipart/form-data body keyed by literal field name.

    Field names like `image[]` survive verbatim — Python's email parser
    treats the multipart `name=` parameter as an opaque token and doesn't
    URL-decode or re-quote the brackets.
    """
    prefix = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode()
    msg = BytesParser(policy=default).parsebytes(prefix + body)
    fields: dict[str, list[bytes]] = {}
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not isinstance(name, str):
            continue
        payload = part.get_payload(decode=True)
        assert isinstance(payload, bytes), f"unexpected non-bytes payload for {name!r}"
        fields.setdefault(name, []).append(payload)
    return fields


def _assert_subset(fields: set[str], allowed: frozenset[str], *, where: str) -> None:
    extras = fields - allowed
    assert not extras, (
        f"{where}: unexpected fields in wire body (not in gpt-image schema): {sorted(extras)}"
    )


def _assert_no_forbidden(fields: set[str], *, where: str) -> None:
    forbidden_present = fields & _FORBIDDEN_FIELDS_ALWAYS
    assert not forbidden_present, (
        f"{where}: forbidden T-042 fields still in wire body: {sorted(forbidden_present)}"
    )


def _assert_quality_value_legal(value: str | None, *, where: str) -> None:
    if value is None:
        return
    assert value in _ALLOWED_QUALITY_VALUES, (
        f"{where}: quality={value!r} is not in gpt-image enum "
        f"{sorted(_ALLOWED_QUALITY_VALUES)} (hd / standard are dall-e-3 legacy)"
    )


def _assert_size_value_legal(value: str | None, *, where: str) -> None:
    if value is None:
        return
    assert value in _ALLOWED_SIZE_VALUES, (
        f"{where}: size={value!r} not in gpt-image enum {sorted(_ALLOWED_SIZE_VALUES)}"
    )


def _quality_in_multipart(fields: dict[str, list[bytes]]) -> str | None:
    if "quality" not in fields:
        return None
    return fields["quality"][0].decode("ascii")


# ----------------------------------------------------------------- tests


async def test_text2image_body_matches_generations_contract(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    captured = _Captured()
    client = _make_client(fake_redis, _capture(captured))
    try:
        # `seed` is accepted by the Python signature but must NOT reach the
        # wire — gpt-image rejects it. Pass a non-None value to prove that
        # caller-supplied seed gets silently dropped.
        await client.generate_image_text2image("a smiling cat", aspect_ratio="2:3", seed=42)
    finally:
        await client.aclose()

    where = "text2image POST /images/generations"
    assert captured.url.endswith("/images/generations"), captured.url
    assert "application/json" in captured.content_type

    body = json.loads(captured.body)
    assert isinstance(body, dict)
    keys = set(body.keys())

    _assert_subset(keys, _ALLOWED_GENERATIONS_FIELDS, where=where)
    _assert_no_forbidden(keys, where=where)

    missing = {"model", "prompt", "size", "n"} - keys
    assert not missing, f"{where}: missing baseline fields {sorted(missing)}"

    _assert_size_value_legal(body.get("size"), where=where)
    _assert_quality_value_legal(body.get("quality"), where=where)
    # 2:3 → portrait 1024x1536; guards against silent enum mapping drift.
    assert body["size"] == "1024x1536"
    assert body["model"] == "gpt-image-2"
    assert body["prompt"] == "a smiling cat"
    assert body["n"] == 1


async def test_image2image_body_matches_edits_contract_single_image(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    captured = _Captured()
    client = _make_client(fake_redis, _capture(captured))
    try:
        await client.generate_image_image2image(
            "make it red", b"raw-png-bytes", aspect_ratio="2:3", seed=99
        )
    finally:
        await client.aclose()

    where = "image2image POST /images/edits (single)"
    assert captured.url.endswith("/images/edits"), captured.url
    content_type = captured.content_type
    assert "multipart/form-data" in content_type

    fields = _parse_multipart(captured.body, content_type)
    field_names = set(fields.keys())

    _assert_subset(field_names, _ALLOWED_EDITS_FIELDS, where=where)
    _assert_no_forbidden(field_names, where=where)
    assert "image" in field_names, f"{where}: missing `image` field"
    assert "image[]" not in field_names, (
        f"{where}: single-image edit must not use `image[]` array shape"
    )
    assert len(fields["image"]) == 1, f"{where}: `image` must appear exactly once"
    assert "mask" not in field_names, f"{where}: image2image must not send `mask`"

    assert fields["model"][0] == b"gpt-image-2"
    assert fields["prompt"][0] == b"make it red"
    assert fields["n"][0] == b"1"
    assert fields["image"][0] == b"raw-png-bytes"
    _assert_size_value_legal(fields["size"][0].decode("ascii"), where=where)
    assert fields["size"][0] == b"1024x1536"
    _assert_quality_value_legal(_quality_in_multipart(fields), where=where)


async def test_inpaint_body_matches_edits_contract(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    captured = _Captured()
    client = _make_client(fake_redis, _capture(captured))
    try:
        await client.generate_image_inpaint(
            "swap shirt", b"image-bytes", b"mask-bytes", aspect_ratio="1:1"
        )
    finally:
        await client.aclose()

    where = "inpaint POST /images/edits"
    assert captured.url.endswith("/images/edits"), captured.url
    content_type = captured.content_type
    fields = _parse_multipart(captured.body, content_type)
    field_names = set(fields.keys())

    _assert_subset(field_names, _ALLOWED_EDITS_FIELDS, where=where)
    _assert_no_forbidden(field_names, where=where)
    assert {"image", "mask"} <= field_names, (
        f"{where}: inpaint must send both `image` and `mask`, got {sorted(field_names)}"
    )
    assert "image[]" not in field_names, f"{where}: inpaint never uses array shape"
    assert len(fields["image"]) == 1
    assert len(fields["mask"]) == 1
    assert fields["image"][0] == b"image-bytes"
    assert fields["mask"][0] == b"mask-bytes"
    _assert_size_value_legal(fields["size"][0].decode("ascii"), where=where)
    _assert_quality_value_legal(_quality_in_multipart(fields), where=where)


async def test_edit_image2image_with_refs_uses_image_array_shape(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """Multi-image edits use `image[]` repeatedly. Bare `image` must NOT
    appear; mixing the two would 400 against the real provider (T-042
    empirical evidence)."""
    captured = _Captured()
    client = _make_client(fake_redis, _capture(captured))
    refs = [b"REF-one", b"REF-two", b"REF-three"]
    try:
        await client.edit_image2image(
            base_image_bytes=b"BASE",
            reference_image_bytes=refs,
            prompt="add a red scarf",
        )
    finally:
        await client.aclose()

    where = "edit_image2image POST /images/edits (multi)"
    content_type = captured.content_type
    fields = _parse_multipart(captured.body, content_type)
    field_names = set(fields.keys())

    _assert_subset(field_names, _ALLOWED_EDITS_FIELDS, where=where)
    _assert_no_forbidden(field_names, where=where)
    assert "image[]" in field_names, f"{where}: multi-image edit must use `image[]` array shape"
    assert "image" not in field_names, f"{where}: must NOT mix bare `image` with `image[]`"
    expected_count = 1 + len(refs)
    assert len(fields["image[]"]) == expected_count, (
        f"{where}: expected {expected_count} `image[]` parts (base + {len(refs)} refs), "
        f"got {len(fields['image[]'])}"
    )
    # `size` is intentionally omitted on edit calls — provider preserves base dims.
    assert "size" not in field_names, (
        f"{where}: edit_image2image must omit `size` so provider preserves base dims"
    )
    _assert_quality_value_legal(_quality_in_multipart(fields), where=where)


async def test_edit_image2image_without_refs_uses_bare_image(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """No references = single image — bare `image` field, never `image[]`."""
    captured = _Captured()
    client = _make_client(fake_redis, _capture(captured))
    try:
        await client.edit_image2image(
            base_image_bytes=b"only-base",
            reference_image_bytes=None,
            prompt="restyle",
        )
    finally:
        await client.aclose()

    where = "edit_image2image POST /images/edits (no refs)"
    content_type = captured.content_type
    fields = _parse_multipart(captured.body, content_type)
    field_names = set(fields.keys())

    _assert_subset(field_names, _ALLOWED_EDITS_FIELDS, where=where)
    _assert_no_forbidden(field_names, where=where)
    assert field_names & {"image", "image[]"} == {"image"}, (
        f"{where}: single-image edit must use bare `image`, not `image[]`. "
        f"Got {sorted(field_names & {'image', 'image[]'})}"
    )
    assert len(fields["image"]) == 1
    assert "size" not in field_names, (
        f"{where}: edit_image2image must omit `size` so provider preserves base dims"
    )
    _assert_quality_value_legal(_quality_in_multipart(fields), where=where)


async def test_edit_inpaint_body_matches_edits_contract(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    base = _make_rgba_png(64, 96, alpha=255)
    mask = _make_rgba_png(64, 96, alpha=0)
    captured = _Captured()
    client = _make_client(fake_redis, _capture(captured))
    try:
        await client.edit_inpaint(
            base_image_bytes=base, mask_png_bytes=mask, prompt="erase background"
        )
    finally:
        await client.aclose()

    where = "edit_inpaint POST /images/edits"
    content_type = captured.content_type
    fields = _parse_multipart(captured.body, content_type)
    field_names = set(fields.keys())

    _assert_subset(field_names, _ALLOWED_EDITS_FIELDS, where=where)
    _assert_no_forbidden(field_names, where=where)
    assert {"image", "mask"} <= field_names, (
        f"{where}: inpaint must send `image` + `mask`; got {sorted(field_names)}"
    )
    assert "image[]" not in field_names, f"{where}: inpaint never uses array shape"
    assert len(fields["image"]) == 1
    assert len(fields["mask"]) == 1
    # Provider preserves base dims; `size` is intentionally absent.
    assert "size" not in field_names
    _assert_quality_value_legal(_quality_in_multipart(fields), where=where)
