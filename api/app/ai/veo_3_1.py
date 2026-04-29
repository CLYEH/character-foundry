"""Veo31Client — real provider client for Veo 3.1 i2v (T-029).

Veo follows Google's long-running-operation pattern:

  1. POST `{api_url}/models/{model}:predictLongRunning` → returns
     `{"name": "models/veo-3.1/operations/<id>"}`.
  2. GET `{api_url}/{operation_name}` repeatedly until `done: true`.
  3. The terminal response contains either `bytesBase64Encoded` (inline)
     or `videoUri` (download separately).

This wrapper layers the same resilience primitives as `GptImage2Client`:

  - Per-HTTP-request timeout (`VEO_TIMEOUT_MS`)
  - Bounded retry on transient failures (timeout / 5xx / 429); 4xx fails fast
  - Per-model circuit breaker (`circuit:veo-3.1`)
  - Provider error → AgentError translation (`app.ai.errors`)

Identity-preservation trick (DECISIONS §3): the same `image_bytes` is sent
as BOTH `image` (first frame) and `lastFrame`. Veo anchors generation to
both ends, so the character's look is locked at start and end of the clip.
Callers (workers / API routes) stay oblivious — they just hand over the
parent image and a prompt.

The retry envelope ONLY protects the submit step. Once Veo accepts the
long-running operation it has started consuming budget (planning §4.4:
"影片重試很貴"); resubmitting after a poll-time transient would throw away
an in-flight generation and pay for a fresh one. Post-submit failures
propagate directly and still feed the breaker if retryable, so upstream
health remains observable.

A failed submission (after exhausting retries) counts as a single breaker
failure — same policy as the image client. Non-retryable errors (validation,
content policy, quota exhaustion) never feed the breaker.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from dataclasses import dataclass
from typing import Any

import httpx
from redis.asyncio import Redis

from app.ai import config
from app.ai.base import VeoResult
from app.ai.circuit import CircuitBreaker
from app.ai.errors import (
    map_exception_to_agent_error,
    map_response_to_agent_error,
    model_invalid_request,
    model_quota_exceeded,
    model_timeout,
    model_unavailable,
)
from app.core.errors import AgentErrorException

_logger = logging.getLogger(__name__)

# Service name used for the circuit breaker key (`degraded:veo-3.1`).
# Decoupled from the env-driven `VEO_MODEL` knob the same way the
# reconciler uses a fixed `RECONCILER_SERVICE_NAME`: `/v1/meta` should keep
# rendering the same `service` identifier even after we rotate model SKUs.
VEO_SERVICE_NAME = "veo-3.1"

# Env var name surfaced in operator-facing auth-failure remediation. Threaded
# into `map_response_to_agent_error` so a Veo 401/403 says "rotate VEO_API_KEY"
# rather than the default `OPENAI_API_KEY` message — Codex P2 round-5.
_VEO_AUTH_KEY_ENV = "VEO_API_KEY"


class Veo31Client:
    """Implements `app.ai.base.VideoClient` against the Veo 3.1 long-running API."""

    def __init__(
        self,
        redis: Redis,
        *,
        api_key: str | None = None,
        api_url: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
        poll_interval_seconds: float | None = None,
        max_poll_attempts: int | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.redis = redis
        self.api_key = api_key if api_key is not None else config.veo_api_key()
        self.api_url = (api_url or config.veo_api_url()).rstrip("/")
        self.model = model or config.veo_model()
        self.timeout_seconds = (
            timeout_seconds if timeout_seconds is not None else config.veo_timeout_seconds()
        )
        self.max_retries = max_retries if max_retries is not None else config.veo_max_retries()
        self.poll_interval_seconds = (
            poll_interval_seconds
            if poll_interval_seconds is not None
            else config.veo_poll_interval_seconds()
        )
        self.max_poll_attempts = (
            max_poll_attempts if max_poll_attempts is not None else config.veo_max_poll_attempts()
        )
        self._http_client = http_client
        self._owns_client = http_client is None
        self._breaker = CircuitBreaker(VEO_SERVICE_NAME, redis)

    async def aclose(self) -> None:
        if self._http_client is not None and self._owns_client:
            await self._http_client.aclose()
            self._http_client = None

    # ------------------------------------------------------------------ public

    async def generate_i2v(
        self,
        *,
        image_bytes: bytes,
        prompt: str,
        duration_seconds: float | None = None,
    ) -> VeoResult:
        """Run a Veo i2v generation: retry submission, then poll + download once.

        Retry only protects the *submit* step. Once Veo accepts the long-
        running operation it's already started consuming provider budget
        (planning §4.4: "影片重試很貴"); resubmitting after a poll-time
        transient would throw away an in-flight generation and pay for a
        new one. Post-submit failures propagate directly to the caller and
        still feed the breaker if retryable, so upstream health continues
        to be tracked.
        """
        await self._breaker.raise_if_open()

        operation_name = await self._submit_with_retry(
            image_bytes=image_bytes,
            prompt=prompt,
            duration_seconds=duration_seconds,
        )

        try:
            operation = await self._poll_until_done(operation_name)
            video_bytes = await self._fetch_video_bytes(operation)
        except AgentErrorException as exc:
            # Retryable post-submit failures still tell us upstream is sick;
            # non-retryable ones (INVALID_ARGUMENT, RESOURCE_EXHAUSTED) are
            # request / quota issues and must not count toward OPEN.
            if exc.error.retryable:
                await self._breaker.record_failure()
            raise

        await self._breaker.record_success()

        # Veo honours the requested durationSeconds deterministically; if the
        # caller didn't supply one, fall back to whatever the operation
        # response advertises so callers / GenerationLog still see a value.
        effective_duration = (
            duration_seconds
            if duration_seconds is not None
            else _extract_duration_seconds(operation)
        )
        duration_ms = int((effective_duration or 0.0) * 1000)

        return VeoResult(
            video_bytes=video_bytes,
            model_version=str(_extract_model_version(operation) or self.model),
            duration_ms=duration_ms,
            generation_log_payload={
                "model": self.model,
                "operation_name": operation_name,
                "duration_seconds": effective_duration,
                # Keep the trailing operation envelope minus the bytes payload
                # — workers can drop this straight into GenerationLog.raw_response.
                "operation": _redacted_operation(operation),
            },
        )

    # ----------------------------------------------------------------- private

    async def _submit_with_retry(
        self,
        *,
        image_bytes: bytes,
        prompt: str,
        duration_seconds: float | None,
    ) -> str:
        """Submit until success or retry budget exhausted, returning the
        operation name. Mirrors `GptImage2Client._call_with_resilience`'s
        accounting: a failed call (after exhausting retries) counts as one
        breaker failure regardless of attempt count, and only retryable
        errors feed the breaker."""
        last_error: AgentErrorException | None = None
        attempts = self.max_retries + 1

        for attempt in range(attempts):
            try:
                return await self._submit_job(
                    image_bytes=image_bytes,
                    prompt=prompt,
                    duration_seconds=duration_seconds,
                )
            except AgentErrorException as exc:
                last_error = exc
                if not exc.error.retryable or attempt == attempts - 1:
                    break
                await self._sleep_for_retry(attempt)

        assert last_error is not None
        if last_error.error.retryable:
            await self._breaker.record_failure()
        raise last_error

    async def _submit_job(
        self,
        *,
        image_bytes: bytes,
        prompt: str,
        duration_seconds: float | None,
    ) -> str:
        # First frame == last frame: identity anchor (DECISIONS §3 / planning §4.2).
        b64_image = base64.b64encode(image_bytes).decode("ascii")
        image_payload = {"bytesBase64Encoded": b64_image, "mimeType": "image/png"}

        instance: dict[str, Any] = {
            "prompt": prompt,
            "image": image_payload,
            "lastFrame": image_payload,
        }
        # No `personGeneration` is sent by default. The earlier draft
        # hardcoded `allow_all` (per planning §4.2 speculation) but Veo i2v
        # rejects that value, and the per-region rules (EU/UK/CH/MENA require
        # `allow_adult`) make a single static default wrong everywhere.
        # Letting Veo apply its server-side default is the safer Phase 1
        # stance; per-region configurability is deferred until we have a
        # real deployment to tune for (Codex P1 round-4 on PR #39).
        parameters: dict[str, Any] = {}
        if duration_seconds is not None:
            parameters["durationSeconds"] = duration_seconds

        body = {"instances": [instance], "parameters": parameters}
        path = f"models/{self.model}:predictLongRunning"

        response = await self._post_json(path, body)
        if not response.is_success:
            raise map_response_to_agent_error(
                self.model, response, auth_key_env_var=_VEO_AUTH_KEY_ENV
            )

        payload = _safe_json(response)
        name = payload.get("name") if isinstance(payload, dict) else None
        if not isinstance(name, str) or not name.strip():
            raise model_invalid_request(
                self.model, detail="submit response missing operation `name`"
            )
        return name

    async def _poll_until_done(self, operation_name: str) -> dict[str, Any]:
        """Poll the operation endpoint until `done: true`.

        Each poll is its own HTTP request with the same per-request timeout
        as submission; transient failures during polling propagate up to the
        outer retry envelope (which restarts the whole flow). A stuck
        operation that never flips `done` raises MODEL_TIMEOUT once
        `max_poll_attempts × poll_interval_seconds` elapses.
        """
        for _ in range(self.max_poll_attempts):
            response = await self._get(operation_name)
            if not response.is_success:
                raise map_response_to_agent_error(
                    self.model, response, auth_key_env_var=_VEO_AUTH_KEY_ENV
                )

            payload = _safe_json(response)
            if not isinstance(payload, dict):
                raise model_invalid_request(
                    self.model, detail="poll response was not a JSON object"
                )

            if payload.get("done") is True:
                error = payload.get("error")
                if isinstance(error, dict):
                    raise _map_operation_error(self.model, error)
                return payload

            await asyncio.sleep(self.poll_interval_seconds)

        raise model_timeout(
            self.model,
            cause=f"Veo operation {operation_name} did not finish within "
            f"{self.max_poll_attempts} polls × {self.poll_interval_seconds:g}s",
        )

    async def _fetch_video_bytes(self, operation: dict[str, Any]) -> bytes:
        """Pull the mp4 bytes out of the terminal operation envelope.

        `_extract_videos` normalises Veo's two response shapes (direct
        `response.videos[]` and nested `response.generateVideoResponse
        .generatedSamples[].video`) into a single list of `_VideoItem`
        records — so the inline-vs-uri branching here only deals with
        ONE schema regardless of which payload Veo returned.
        """
        items = _extract_videos(operation)
        if not items:
            raise model_invalid_request(
                self.model, detail="operation.response did not include any video items"
            )

        first = items[0]
        if first.bytes_base64:
            try:
                return base64.b64decode(first.bytes_base64)
            except (ValueError, TypeError) as exc:
                raise map_exception_to_agent_error(
                    self.model, RuntimeError(f"video b64 decode failed: {exc}")
                ) from exc

        if first.uri:
            return await self._download_video_uri(first.uri)

        raise model_invalid_request(
            self.model,
            detail="video item missing both bytesBase64Encoded and uri",
        )

    async def _download_video_uri(self, url: str) -> bytes:
        """Fetch the bytes from a `videoUri` returned by a completed operation.

        Per the documented Gemini Veo flow the videoUri is fetched WITH the
        provider API key (the URI is a Gemini API endpoint, not an arbitrary
        signed CDN URL) and may redirect to the actual media location. We
        keep the auth header and follow redirects.

        Codex P1 round-3 superseded the round-1 strip-the-key prescription,
        which was based on the inverted assumption that videoUri pointed at
        a signed CDN. With the key restored, 401/403 from this endpoint
        correctly maps through the standard provider-error mapper to
        `INTERNAL_AUTH_FAILED` (matching the submit/poll path's mapping).

        Cross-host redirect hardening (stripping the API key when redirected
        to a different host) is deferred to real-Veo integration time, when
        we can observe the actual redirect chain. Phase 1 stub mode never
        exercises this path, and the breaker still catches degraded
        behaviour if a real-mode deployment hits 5xx on the blob host.
        """
        client = await self._http()
        try:
            response = await client.get(url, follow_redirects=True)
        except httpx.HTTPError as exc:
            raise map_exception_to_agent_error(self.model, exc) from exc
        if not response.is_success:
            raise map_response_to_agent_error(
                self.model, response, auth_key_env_var=_VEO_AUTH_KEY_ENV
            )
        return response.content

    # -- HTTP plumbing -------------------------------------------------------

    async def _http(self) -> httpx.AsyncClient:
        if self._http_client is None:
            # Veo / Vertex auth is `x-goog-api-key`. All endpoints
            # (submit, poll, videoUri download) go through this client and
            # carry the header. See `_download_video_uri` for the Phase 1
            # rationale on cross-host redirect handling.
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout_seconds),
                headers={"x-goog-api-key": self.api_key or ""},
            )
        return self._http_client

    def _resolve_url(self, path: str) -> str:
        # `path` may be either a relative API path ("models/.../predictLongRunning")
        # OR a fully-qualified operation name returned by the submit response
        # ("models/veo-3.1/operations/abc123"). Both join cleanly under the
        # `api_url` root, but operation names sometimes arrive starting with
        # "operations/..." too — strip leading slashes and concatenate.
        if path.startswith(("http://", "https://")):
            return path
        return f"{self.api_url}/{path.lstrip('/')}"

    async def _post_json(self, path: str, body: dict[str, Any]) -> httpx.Response:
        client = await self._http()
        try:
            return await client.post(self._resolve_url(path), json=body)
        except httpx.HTTPError as exc:
            raise map_exception_to_agent_error(self.model, exc) from exc

    async def _get(self, path: str) -> httpx.Response:
        client = await self._http()
        try:
            return await client.get(self._resolve_url(path))
        except httpx.HTTPError as exc:
            raise map_exception_to_agent_error(self.model, exc) from exc

    async def _sleep_for_retry(self, attempt: int) -> None:
        # Veo doesn't currently set Retry-After on operation polling errors
        # (operation responses are JSON, not HTTP errors), so just use
        # exponential backoff. Identical shape to GptImage2Client._sleep_for_retry.
        delay = min(2.0**attempt, 8.0)
        await asyncio.sleep(delay)


# ---------------------------------------------------------------------------
# Operation-payload helpers — kept module-level so they're easy to unit test
# without the breaker / HTTP scaffolding.
# ---------------------------------------------------------------------------


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except (ValueError, TypeError):
        return None


@dataclass(frozen=True)
class _VideoItem:
    """Normalised view of one video result, regardless of which Veo response
    shape it came from. Either `bytes_base64` or `uri` will be set on a
    well-formed payload (caller validates downstream)."""

    uri: str | None
    bytes_base64: str | None
    duration_seconds: float | None


def _extract_videos(operation: dict[str, Any]) -> list[_VideoItem]:
    """Normalise Veo's terminal-operation video payload to a single list.

    Two shapes are observed in current Google Veo REST examples (Codex P1
    round-2 + round-4 on PR #39):

    1. **Direct** — `response.videos[]` with
       `{bytesBase64Encoded, videoUri, durationSeconds}`.
    2. **Nested** — `response.generateVideoResponse.generatedSamples[].video`
       with the inner schema using `uri` instead of `videoUri`.

    Round-2 caught that supporting both shapes inconsistently was worse
    than picking one. The adapter ground-truth round-4 supplied lets us
    flip back to full normalisation, but only via this single chokepoint
    so downstream helpers (`_fetch_video_bytes`,
    `_extract_duration_seconds`) never branch on shape.
    """
    response = operation.get("response")
    if not isinstance(response, dict):
        return []

    items: list[_VideoItem] = []

    direct = response.get("videos")
    if isinstance(direct, list):
        for v in direct:
            if isinstance(v, dict):
                items.append(_video_item_from(v, uri_keys=("videoUri",)))

    nested = response.get("generateVideoResponse")
    if isinstance(nested, dict):
        samples = nested.get("generatedSamples")
        if isinstance(samples, list):
            for sample in samples:
                if not isinstance(sample, dict):
                    continue
                video = sample.get("video")
                if isinstance(video, dict):
                    # `generatedSamples` uses `uri` on the inner object;
                    # tolerate `videoUri` too in case Google unifies them.
                    items.append(_video_item_from(video, uri_keys=("uri", "videoUri")))

    return items


def _video_item_from(video: dict[str, Any], *, uri_keys: tuple[str, ...]) -> _VideoItem:
    uri: str | None = None
    for key in uri_keys:
        value = video.get(key)
        if isinstance(value, str) and value.strip():
            uri = value
            break
    b64 = video.get("bytesBase64Encoded")
    if not isinstance(b64, str) or not b64:
        b64 = None
    duration_raw = video.get("durationSeconds")
    duration = float(duration_raw) if isinstance(duration_raw, int | float) else None
    return _VideoItem(uri=uri, bytes_base64=b64, duration_seconds=duration)


def _extract_model_version(operation: dict[str, Any]) -> str | None:
    metadata = operation.get("metadata")
    if isinstance(metadata, dict):
        version = metadata.get("model") or metadata.get("modelVersion")
        if isinstance(version, str):
            return version
    return None


def _extract_duration_seconds(operation: dict[str, Any]) -> float | None:
    """Return the first non-null `durationSeconds` from any normalised video
    item. Reuses `_extract_videos` so both response shapes are covered with
    one source of truth."""
    for item in _extract_videos(operation):
        if item.duration_seconds is not None:
            return item.duration_seconds
    return None


def _redacted_operation(operation: dict[str, Any]) -> dict[str, Any]:
    """Strip the (potentially multi-MB) base64 video payload out of the
    operation envelope so workers can persist a useful audit record without
    bloating GenerationLog or leaking raw media into log storage.

    Recursive — drops any `bytesBase64Encoded` key wherever it appears in
    the response tree. Catches both shapes the client supports:
      - `response.videos[].bytesBase64Encoded`
      - `response.generateVideoResponse.generatedSamples[].video.bytesBase64Encoded`
    plus any future shape Veo introduces. Codex P2 round-5 caught that the
    earlier per-shape redactor missed the nested path once round-4 added
    that path back to `_extract_videos`.

    Only `bytesBase64Encoded` is removed; URIs, durations, model metadata,
    and everything else stay so the audit record remains useful.
    """
    scrubbed = _scrub_inline_bytes(operation)
    # `_scrub_inline_bytes` recurses on Any, but the top-level operation is
    # always a dict — narrow the type for mypy without runtime cost.
    assert isinstance(scrubbed, dict)
    return scrubbed


def _scrub_inline_bytes(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _scrub_inline_bytes(v) for k, v in node.items() if k != "bytesBase64Encoded"}
    if isinstance(node, list):
        return [_scrub_inline_bytes(item) for item in node]
    return node


def _map_operation_error(model: str, error: dict[str, Any]) -> AgentErrorException:
    """Translate a Veo operation-level error into the matching AgentError.

    Veo / Vertex use Google API-style status strings (`INVALID_ARGUMENT`,
    `RESOURCE_EXHAUSTED`, …). Map the ones planning §4.4 calls out
    explicitly; everything else falls back to MODEL_UNAVAILABLE so the
    breaker can react.
    """
    status = error.get("status") if isinstance(error.get("status"), str) else None
    code = error.get("code")
    message = error.get("message") if isinstance(error.get("message"), str) else None

    if status == "INVALID_ARGUMENT":
        return model_invalid_request(model, detail=message)
    if status == "RESOURCE_EXHAUSTED":
        return model_quota_exceeded(model, detail=message)

    detail = message or status or (str(code) if code is not None else None)
    return model_unavailable(model, cause=f"Veo operation error: {detail or 'unknown'}")
