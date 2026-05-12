"""Nightly contract replay against live AI providers (T-058).

`tests/ai/test_gpt_image_2_contract.py` (T-044) pins the *outgoing* wire
body to the gpt-image schema using `httpx.MockTransport`. That guards
against the bug class T-042 fixed (we sent fields the provider rejects, so
*outgoing* shape was wrong). This module covers the inverse: the *incoming*
response shape silently shifts under us — the pattern T-045 (gpt-5-mini
`max_completion_tokens` rename + reasoning-model content drift) and T-051
(Veo `done: true` + RAI fields, no `videos`) hit. Code assumed shape X,
provider returned shape Y, and the change landed in production through
normal CI because no test exercised real providers.

This module hits the three live providers (gpt-image-2 / gpt-5-mini /
Veo 3.1) with the cheapest possible real call and asserts only the response
*shape*, never content semantics. It runs on the nightly
`.github/workflows/provider-contract.yml` schedule with dedicated test API
keys; default `pytest` skips it via the `addopts = -m "not real_provider"`
default in `pyproject.toml`.

For each provider the shape-check function is split out so the drift cases
below can exercise the same invariant on a fabricated payload without
touching the network. That's how acceptance criterion §2 ("each contract
replay test can fail out a drift scenario") is met.

Veo splits into two legal shapes:

- **Shape A — success.** `done=true`, `response.videos` non-empty.
- **Shape B — RAI filtered (T-051).** `done=true`, `raiMediaFilteredCount
  >= 1`, `raiMediaFilteredReasons: list[str]`, `videos` not required.

Either shape passes. Drift is "neither A nor B" — a field rename, type
change, or a new terminal state we don't yet model. The drift assertion
deliberately doesn't write `assert "videos" in payload` style invariants,
because that would mark every RAI-filtered nightly run as a false drift.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from importlib import resources
from typing import Any

import httpx
import pytest

# --------------------------------------------------------------------- helpers


class ContractDriftError(AssertionError):
    """Raised when a provider response no longer matches its expected shape.

    Subclasses `AssertionError` so pytest renders it cleanly in the test
    report and the `provider-contract.yml` workflow can scrape the message
    into the auto-filed `provider-drift` issue body. The message includes
    the full payload (no API keys are echoed — they live in request
    headers, never the response) so the on-call eyeballing the issue can
    tell real drift from a transient provider 5xx."""


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} not set — real_provider tests require live keys")
    return value


def _drift(message: str, payload: Any) -> ContractDriftError:
    return ContractDriftError(f"{message}\n\nraw payload:\n{payload!r}")


# ----------------------------------------------------------- shape assertions


def assert_gpt_image_generation_shape(payload: Any) -> None:
    """Pins the `/v1/images/generations` response for gpt-image-2.

    Source of truth: `app.ai.gpt_image_2.GptImage2Client._parse_success`
    reads `payload.data[0].b64_json`. Anything else is drift the client
    would 500 on. `model` is included in the live response but the parser
    falls back to the configured SKU, so we don't pin it here.
    """
    if not isinstance(payload, dict):
        raise _drift("gpt-image-2: top-level payload is not a JSON object", payload)
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        raise _drift("gpt-image-2: `data` missing or empty", payload)
    first = data[0]
    if not isinstance(first, dict):
        raise _drift("gpt-image-2: `data[0]` is not an object", payload)
    b64 = first.get("b64_json")
    if not isinstance(b64, str) or not b64:
        raise _drift("gpt-image-2: `data[0].b64_json` missing / not a non-empty string", payload)


def assert_chat_completion_shape(payload: Any) -> None:
    """Pins `/v1/chat/completions` for gpt-5-mini in `json_object` mode.

    Source of truth: `app.ai.reconciler_client.Gpt5MiniClient._parse_chat_json`.
    Mirrors three invariants from the prod parser, in order:

    1. `choices[0].message.content` is a non-empty string OR a list of
       `{type: "text", text: str}` parts whose concatenation is non-empty.
    2. That string parses via `json.loads`.
    3. The parsed result is a JSON object (dict).

    The third invariant is what prevents the false-negative class Codex
    review round-2 (PR #76) flagged: the provider could regress to
    returning plain text or whitespace under `response_format:
    json_object`, our shape check would pass (non-empty string), but
    every prod request would 5xx at `json.loads`. Asserting JSON-
    decodability + dict-shape catches that drift class.

    JSON-decodability is structural, not semantic — we don't inspect the
    parsed object's keys / values. "Is this JSON I can parse?" sits on
    the same side of the line as "does this field exist?".

    On refusal handling: the prod parser also accepts `finish_reason:
    content_filter` and `message.refusal: str` as legal terminal states
    (T-045 / Codex round-3/4). This shape check stays structural — a
    refusal whose `content` is a valid JSON object will pass silently
    (the test prompt is too benign for that to be a likely real
    response). A *structurally degraded* refusal — null content, empty
    text-parts, plain-text content, or non-object JSON — surfaces as
    drift, which is the right behaviour: if our trivial probe trips one
    of those, something has shifted worth investigating.
    """
    if not isinstance(payload, dict):
        raise _drift("gpt-5-mini: top-level payload is not a JSON object", payload)
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise _drift("gpt-5-mini: `choices` missing or empty", payload)
    first = choices[0]
    if not isinstance(first, dict):
        raise _drift("gpt-5-mini: `choices[0]` is not an object", payload)
    message = first.get("message")
    if not isinstance(message, dict):
        raise _drift("gpt-5-mini: `choices[0].message` missing or not an object", payload)

    raw_content = message.get("content")
    if isinstance(raw_content, str):
        text = raw_content
    elif isinstance(raw_content, list):
        # Mirror prod parser: concatenate all text parts. A payload like
        # `[{"type":"text","text":""}]` joins to `""` and fails downstream.
        text = "".join(
            part["text"]
            for part in raw_content
            if isinstance(part, dict)
            and part.get("type") == "text"
            and isinstance(part.get("text"), str)
        )
    else:
        raise _drift(
            "gpt-5-mini: `message.content` missing / not a string or text-parts list",
            payload,
        )

    if not text:
        raise _drift(
            "gpt-5-mini: `message.content` resolved to empty string "
            "(missing content, only-empty text-parts, etc)",
            payload,
        )
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise _drift(
            f"gpt-5-mini: `message.content` is not valid JSON "
            f"(json.loads error: {exc}); prod `_parse_chat_json` would 5xx here",
            payload,
        ) from None
    if not isinstance(parsed, dict):
        raise _drift(
            f"gpt-5-mini: `message.content` parsed as {type(parsed).__name__}, "
            "expected JSON object (dict)",
            payload,
        )


def _rai_fields_present(container: Any) -> bool:
    if not isinstance(container, dict):
        return False
    count = container.get("raiMediaFilteredCount")
    reasons = container.get("raiMediaFilteredReasons")
    if not isinstance(count, int) or count < 1:
        return False
    if not isinstance(reasons, list) or not reasons:
        return False
    return all(isinstance(r, str) for r in reasons)


def _video_item_payload_present(video: Any) -> bool:
    """A video item must carry at least one of `videoUri` / `uri` (string)
    or `bytesBase64Encoded` (string) — otherwise the prod parser
    `_fetch_video_bytes` in `app.ai.veo_3_1` would raise `model_invalid_request
    ("video item missing both bytesBase64Encoded and uri")`. Asserting
    payload presence here catches the "schema looks right but is hollow"
    drift class — e.g. provider returns `videos: [{}]` or renames both
    payload fields simultaneously."""
    if not isinstance(video, dict):
        return False
    uri = video.get("videoUri") or video.get("uri")
    if isinstance(uri, str) and uri:
        return True
    b64 = video.get("bytesBase64Encoded")
    return isinstance(b64, str) and bool(b64)


def _videos_present(response: Any) -> bool:
    """Mirror prod `Veo31Client._fetch_video_bytes`: it reads `items[0]`
    only (after `_extract_videos` normalises both direct and nested shapes
    into a single list, direct first). If `items[0]` is hollow it raises
    `model_invalid_request`, regardless of whether later items would have
    been valid.

    Codex PR #76 review round-4: an earlier `any(...)` check accepted a
    mixed `videos: [{}, {real}]` payload that prod would still 5xx on
    (item[0] is empty). Match the prod ordering: prefer the first direct
    video, else the first nested sample's `video`, and require it carry
    payload."""
    if not isinstance(response, dict):
        return False
    direct = response.get("videos")
    if isinstance(direct, list) and direct:
        # `_extract_videos` skips non-dict entries when building `items`
        # — find the first that would be normalised in, then require it
        # carry payload (mirrors prod `items[0]` lookup).
        first_direct = next((v for v in direct if isinstance(v, dict)), None)
        if first_direct is not None:
            return _video_item_payload_present(first_direct)
        # `direct` was non-empty but had no dict entries — fall through
        # to the nested path (matches prod, where the direct loop would
        # have appended nothing and `items[0]` becomes the nested one).
    nested = response.get("generateVideoResponse")
    if isinstance(nested, dict):
        samples = nested.get("generatedSamples")
        if isinstance(samples, list) and samples:
            first_sample = next((s for s in samples if isinstance(s, dict)), None)
            if first_sample is not None:
                return _video_item_payload_present(first_sample.get("video"))
    return False


def assert_veo_terminal_shape(payload: Any) -> None:
    """Veo 3.1 terminal-operation envelope: accept Shape A OR Shape B.

    - **Shape A** — success: `done=true`, `response.videos` (or the nested
      `generateVideoResponse.generatedSamples`) non-empty. `app.ai.veo_3_1
      ._extract_videos` covers both nesting shapes.
    - **Shape B** — RAI filter (T-051): `done=true`,
      `raiMediaFilteredCount >= 1`, `raiMediaFilteredReasons: list[str]`.
      `videos` may be absent or empty. RAI fields can live either at the
      top level of `response` or (older shape) at the operation root;
      accept either.

    Drift is "neither A nor B" — a renamed field, a new terminal status,
    or a type change. A literal `assert "videos" in payload` would
    misclassify every RAI nightly run as drift, so we explicitly
    short-circuit on RAI before requiring the videos field.
    """
    if not isinstance(payload, dict):
        raise _drift("Veo: top-level payload is not a JSON object", payload)
    if payload.get("done") is not True:
        raise _drift("Veo: terminal payload must have `done: true`", payload)
    if isinstance(payload.get("error"), dict):
        raise _drift("Veo: terminal payload reports an operation-level error", payload)
    response = payload.get("response")
    if not isinstance(response, dict):
        raise _drift("Veo: terminal payload missing `response` object", payload)
    if _rai_fields_present(response) or _rai_fields_present(payload):
        return
    if _videos_present(response):
        return
    raise _drift(
        "Veo: terminal payload matched neither Shape A (videos[]) nor Shape B (raiMediaFilteredCount)",
        payload,
    )


# ------------------------------------------------------- real-provider tests


_OPENAI_API_BASE = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1")
_VEO_API_URL = os.environ.get("VEO_API_URL", "https://generativelanguage.googleapis.com/v1beta")
_VEO_MODEL = os.environ.get("VEO_MODEL", "veo-3.1-generate-preview")

# Per-request timeout for the real-provider calls. Veo predictLongRunning's
# submit step is fast (sub-second); the polling loop sleeps between polls.
# 60s gives enough slack for transient network conditions on GH Actions
# runners.
_REAL_CALL_TIMEOUT_S = 60.0


@pytest.mark.real_provider
async def test_gpt_image_2_real_response_shape() -> None:
    """Cheapest real call to `/images/generations` — `quality=low` minimises
    spend per call (cents, not dollars) while still exercising the same
    success path the client decodes."""
    api_key = _require_env("OPENAI_API_KEY")
    async with httpx.AsyncClient(
        base_url=_OPENAI_API_BASE,
        timeout=_REAL_CALL_TIMEOUT_S,
        headers={"Authorization": f"Bearer {api_key}"},
    ) as client:
        response = await client.post(
            f"{_OPENAI_API_BASE}/images/generations",
            json={
                "model": "gpt-image-2",
                "prompt": "a small red square on a white background",
                "size": "1024x1024",
                "quality": "low",
                "n": 1,
            },
        )
        assert response.status_code == 200, (
            f"gpt-image-2 returned HTTP {response.status_code}: {response.text!r}"
        )
        assert_gpt_image_generation_shape(response.json())


@pytest.mark.real_provider
async def test_gpt_5_mini_real_response_shape() -> None:
    """Minimal JSON-mode chat call. Mirrors the wire shape the reconciler
    client sends (`max_completion_tokens`, `response_format: json_object`,
    no `temperature` — see T-045).

    `max_completion_tokens` is set to 512 rather than the bare minimum
    because gpt-5-mini is a *reasoning* model: it consumes
    `reasoning_tokens` from the same budget before emitting visible
    `content`. Empirically (probed 2026-05-12), a 64-token cap burned all
    64 tokens on reasoning and returned `content: ""` with
    `finish_reason: "length"` — a structurally degraded payload that the
    reconciler-client would surface as `MODEL_RESPONSE_TRUNCATED`. The
    real-prod `RECONCILER_MAX_TOKENS` default is 800; 512 gives enough
    slack for the reasoning overhead on a trivial prompt while keeping
    cost well under a cent."""
    api_key = _require_env("OPENAI_API_KEY")
    async with httpx.AsyncClient(
        base_url=_OPENAI_API_BASE,
        timeout=_REAL_CALL_TIMEOUT_S,
        headers={"Authorization": f"Bearer {api_key}"},
    ) as client:
        response = await client.post(
            f"{_OPENAI_API_BASE}/chat/completions",
            json={
                "model": "gpt-5-mini",
                "messages": [
                    {"role": "system", "content": 'Reply with the JSON object {"ok": true}.'},
                    {"role": "user", "content": "ping"},
                ],
                "max_completion_tokens": 512,
                "response_format": {"type": "json_object"},
            },
        )
        assert response.status_code == 200, (
            f"gpt-5-mini returned HTTP {response.status_code}: {response.text!r}"
        )
        assert_chat_completion_shape(response.json())


@pytest.mark.real_provider
async def test_veo_3_1_real_response_shape() -> None:
    """Full Veo i2v submit + poll cycle, smallest legal `durationSeconds`.

    Uses the bundled `sample_base.png` so we don't depend on storage / a
    pre-existing Character. Polls for up to ~3 min; if Veo hasn't returned
    a terminal state by then we surface MODEL_TIMEOUT as drift (something
    is wrong even if the response shape is fine)."""
    api_key = _require_env("VEO_API_KEY")
    image_bytes = resources.files("app.ai._fixtures").joinpath("sample_base.png").read_bytes()
    image_payload = {
        "bytesBase64Encoded": base64.b64encode(image_bytes).decode("ascii"),
        "mimeType": "image/png",
    }
    submit_body: dict[str, Any] = {
        "instances": [
            {
                "prompt": "a still scene with subtle motion",
                "image": image_payload,
                # Identity-preservation trick — see DECISIONS §3 / planning §4.2.
                "lastFrame": image_payload,
            }
        ],
        "parameters": {"durationSeconds": 3},
    }

    async with httpx.AsyncClient(
        timeout=_REAL_CALL_TIMEOUT_S,
        headers={"x-goog-api-key": api_key},
    ) as client:
        submit = await client.post(
            f"{_VEO_API_URL}/models/{_VEO_MODEL}:predictLongRunning",
            json=submit_body,
        )
        assert submit.status_code == 200, (
            f"Veo submit returned HTTP {submit.status_code}: {submit.text!r}"
        )
        submit_payload = submit.json()
        operation_name = submit_payload.get("name") if isinstance(submit_payload, dict) else None
        if not isinstance(operation_name, str) or not operation_name:
            raise _drift("Veo: submit response missing operation `name`", submit_payload)

        # Poll up to ~3 min total. Veo i2v on minimal prompts typically
        # returns in 30-90s; the headroom covers occasional slow runs.
        max_polls = 36
        poll_interval_s = 5.0
        terminal: dict[str, Any] | None = None
        for _ in range(max_polls):
            poll = await client.get(f"{_VEO_API_URL}/{operation_name}")
            assert poll.status_code == 200, (
                f"Veo poll returned HTTP {poll.status_code}: {poll.text!r}"
            )
            poll_payload = poll.json()
            if isinstance(poll_payload, dict) and poll_payload.get("done") is True:
                terminal = poll_payload
                break
            await asyncio.sleep(poll_interval_s)
        if terminal is None:
            raise ContractDriftError(
                f"Veo operation {operation_name} did not reach `done: true` within "
                f"{max_polls} × {poll_interval_s:g}s"
            )
        assert_veo_terminal_shape(terminal)


# ------------------------------------------------------- drift-detection tests
#
# These don't talk to the network. They feed each shape checker a deliberately
# drifted payload and assert it surfaces ContractDriftError. If a future
# refactor accidentally relaxes a check the drift case fails first — and we
# learn about it in CI, before the nightly probe rolls around.


def test_gpt_image_drift_detects_missing_b64_json() -> None:
    drifted = {"data": [{"url": "https://example.test/x.png"}]}
    with pytest.raises(ContractDriftError, match="b64_json"):
        assert_gpt_image_generation_shape(drifted)


def test_gpt_image_drift_detects_empty_data() -> None:
    with pytest.raises(ContractDriftError, match="data"):
        assert_gpt_image_generation_shape({"data": []})


def test_chat_completion_drift_detects_missing_content() -> None:
    drifted = {"choices": [{"message": {"role": "assistant"}}]}
    with pytest.raises(ContractDriftError, match="not a string or text-parts"):
        assert_chat_completion_shape(drifted)


def test_chat_completion_drift_detects_non_text_parts_list() -> None:
    drifted = {
        "choices": [
            {"message": {"role": "assistant", "content": [{"type": "image", "image_url": "x"}]}}
        ]
    }
    with pytest.raises(ContractDriftError, match="empty string"):
        assert_chat_completion_shape(drifted)


def test_chat_completion_drift_detects_empty_text_parts() -> None:
    """`[{"type":"text","text":""}]` is structurally valid but joins to
    `""` and prod's `_parse_chat_json` would crash at `json.loads`.
    False-negative sensor unless we reject the empty join."""
    drifted = {
        "choices": [{"message": {"role": "assistant", "content": [{"type": "text", "text": ""}]}}]
    }
    with pytest.raises(ContractDriftError, match="empty string"):
        assert_chat_completion_shape(drifted)


def test_chat_completion_drift_detects_non_json_plain_text() -> None:
    """Codex review round-2 (PR #76): the provider could regress to
    returning plain text under `response_format: json_object`. Our
    shape check used to pass (non-empty string), but prod
    `_parse_chat_json` runs `json.loads(content)` immediately and would
    5xx. Lock this down."""
    drifted = {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}
    with pytest.raises(ContractDriftError, match="not valid JSON"):
        assert_chat_completion_shape(drifted)


def test_chat_completion_drift_detects_json_array_not_object() -> None:
    """Prod requires the parsed JSON be a dict (object), not a list /
    scalar. A regression that returns `[1, 2]` would parse but prod
    `_parse_chat_json` raises `chat JSON content was not an object`."""
    drifted = {"choices": [{"message": {"role": "assistant", "content": "[1, 2, 3]"}}]}
    with pytest.raises(ContractDriftError, match="expected JSON object"):
        assert_chat_completion_shape(drifted)


def test_chat_completion_drift_detects_whitespace_only_content() -> None:
    """Whitespace-only content is technically a non-empty string but
    `json.loads(" ")` raises ValueError — prod would 5xx."""
    drifted = {"choices": [{"message": {"role": "assistant", "content": "   "}}]}
    with pytest.raises(ContractDriftError, match="not valid JSON"):
        assert_chat_completion_shape(drifted)


def test_chat_completion_accepts_multi_part_text_with_valid_json() -> None:
    """Sanity: concatenation of multiple non-empty text parts whose join
    is a valid JSON object should pass."""
    payload = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": '{"ok": '},
                        {"type": "text", "text": "true}"},
                    ],
                }
            }
        ]
    }
    assert_chat_completion_shape(payload)  # must not raise


def test_chat_completion_accepts_single_string_with_valid_json() -> None:
    """Sanity: the common case — content is a single JSON-object string."""
    payload = {"choices": [{"message": {"role": "assistant", "content": '{"ok": true}'}}]}
    assert_chat_completion_shape(payload)  # must not raise


def test_veo_drift_detects_neither_shape_a_nor_shape_b() -> None:
    drifted = {"done": True, "response": {"videos": [], "newField": "x"}}
    with pytest.raises(ContractDriftError, match="neither Shape A"):
        assert_veo_terminal_shape(drifted)


def test_veo_drift_detects_hollow_video_items() -> None:
    """`videos: [{}]` looks like Shape A structurally but carries no
    `videoUri` / `uri` / `bytesBase64Encoded` — the prod parser
    `_fetch_video_bytes` would 5xx on it. Treat as drift."""
    drifted = {"done": True, "response": {"videos": [{}]}}
    with pytest.raises(ContractDriftError, match="neither Shape A"):
        assert_veo_terminal_shape(drifted)


def test_veo_drift_detects_hollow_first_with_valid_second() -> None:
    """Codex review round-4 (PR #76): `videos[0]={}, videos[1]={valid}`
    would have passed the earlier `any(...)` check but prod
    `_fetch_video_bytes` reads `items[0]` only and raises if hollow,
    regardless of later valid samples. Mirror that: first-item-must-
    carry-payload."""
    drifted = {
        "done": True,
        "response": {
            "videos": [
                {},
                {"videoUri": "https://veo.test/x.mp4"},
            ]
        },
    }
    with pytest.raises(ContractDriftError, match="neither Shape A"):
        assert_veo_terminal_shape(drifted)


def test_veo_drift_detects_hollow_first_nested_sample() -> None:
    """Same first-item rule for the nested `generatedSamples` form."""
    drifted = {
        "done": True,
        "response": {
            "generateVideoResponse": {
                "generatedSamples": [
                    {"video": {}},
                    {"video": {"uri": "https://veo.test/y.mp4"}},
                ]
            }
        },
    }
    with pytest.raises(ContractDriftError, match="neither Shape A"):
        assert_veo_terminal_shape(drifted)


def test_veo_drift_detects_done_false() -> None:
    with pytest.raises(ContractDriftError, match="done"):
        assert_veo_terminal_shape({"done": False, "response": {}})


def test_veo_shape_a_passes() -> None:
    # Shape A — direct `response.videos[]` form. Should NOT raise.
    assert_veo_terminal_shape(
        {
            "done": True,
            "response": {
                "videos": [{"bytesBase64Encoded": "xx", "videoUri": "https://veo.test/x.mp4"}]
            },
        }
    )


def test_veo_shape_a_passes_nested_form() -> None:
    # Shape A — nested `generateVideoResponse.generatedSamples[].video` form.
    assert_veo_terminal_shape(
        {
            "done": True,
            "response": {
                "generateVideoResponse": {
                    "generatedSamples": [{"video": {"uri": "https://veo.test/y.mp4"}}]
                }
            },
        }
    )


def test_veo_shape_b_passes_rai_filtered() -> None:
    # Shape B — `done: true` + RAI fields, no videos. Must NOT raise.
    assert_veo_terminal_shape(
        {
            "done": True,
            "response": {
                "raiMediaFilteredCount": 1,
                "raiMediaFilteredReasons": ["Violates Google's Responsible AI policies."],
            },
        }
    )
