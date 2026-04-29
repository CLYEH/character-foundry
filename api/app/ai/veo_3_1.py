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

The retry loop counts a failed *call* (after exhausting retries) as a single
breaker failure — same policy as the image client, since one flaky call
shouldn't burn the breaker budget by itself. The retry envelopes the whole
submit→poll→download flow rather than each HTTP step; restarting the flow
is the only safe way to recover from a poll-time transient (we can't resume
a half-watched operation from another process).
"""

from __future__ import annotations

import asyncio
import base64
import logging
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
        await self._breaker.raise_if_open()

        last_error: AgentErrorException | None = None
        attempts = self.max_retries + 1

        for attempt in range(attempts):
            try:
                return await self._run_one_attempt(
                    image_bytes=image_bytes,
                    prompt=prompt,
                    duration_seconds=duration_seconds,
                )
            except AgentErrorException as exc:
                last_error = exc
                if not exc.error.retryable or attempt == attempts - 1:
                    break
                await self._sleep_for_retry(attempt)

        # Same policy as GptImage2Client: only retryable errors signal upstream
        # unhealth (timeout / 5xx / 429). Non-retryable ones (validation,
        # auth, content policy) MUST NOT count toward opening the breaker —
        # otherwise a few bad prompts turn into a service-wide outage.
        assert last_error is not None
        if last_error.error.retryable:
            await self._breaker.record_failure()
        raise last_error

    # ----------------------------------------------------------------- private

    async def _run_one_attempt(
        self,
        *,
        image_bytes: bytes,
        prompt: str,
        duration_seconds: float | None,
    ) -> VeoResult:
        """One submit → poll → download pass. Wraps the whole flow so the
        retry envelope at the top level can restart it cleanly.

        On success the breaker is recorded as healthy here so concurrent
        flakes recorded in `record_failure` don't stay stuck across an
        already-good call.
        """
        operation_name = await self._submit_job(
            image_bytes=image_bytes,
            prompt=prompt,
            duration_seconds=duration_seconds,
        )
        operation = await self._poll_until_done(operation_name)
        video_bytes = await self._fetch_video_bytes(operation)

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
        parameters: dict[str, Any] = {
            "personGeneration": "allow_all",
        }
        if duration_seconds is not None:
            parameters["durationSeconds"] = duration_seconds

        body = {"instances": [instance], "parameters": parameters}
        path = f"models/{self.model}:predictLongRunning"

        response = await self._post_json(path, body)
        if not response.is_success:
            raise map_response_to_agent_error(self.model, response)

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
                raise map_response_to_agent_error(self.model, response)

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

        Two shapes are supported because Veo's published responses use
        either the inline `bytesBase64Encoded` form (small clips) or a
        signed `videoUri` (large clips); both have appeared in the public
        examples for Veo 3.x. We try inline first to skip the extra hop.
        """
        videos = _extract_videos(operation)
        if not videos:
            raise model_invalid_request(
                self.model, detail="operation.response did not include any videos[]"
            )

        first = videos[0]
        if not isinstance(first, dict):
            raise model_invalid_request(
                self.model, detail="operation.response.videos[0] was not an object"
            )

        b64_payload = first.get("bytesBase64Encoded")
        if isinstance(b64_payload, str) and b64_payload:
            try:
                return base64.b64decode(b64_payload)
            except (ValueError, TypeError) as exc:
                raise map_exception_to_agent_error(
                    self.model, RuntimeError(f"video b64 decode failed: {exc}")
                ) from exc

        video_uri = first.get("videoUri")
        if not isinstance(video_uri, str) or not video_uri.strip():
            raise model_invalid_request(
                self.model,
                detail="operation.response.videos[0] missing both bytesBase64Encoded and videoUri",
            )

        return await self._download_video_uri(video_uri)

    async def _download_video_uri(self, url: str) -> bytes:
        client = await self._http()
        try:
            response = await client.get(url)
        except httpx.HTTPError as exc:
            raise map_exception_to_agent_error(self.model, exc) from exc
        if not response.is_success:
            raise map_response_to_agent_error(self.model, response)
        return response.content

    # -- HTTP plumbing -------------------------------------------------------

    async def _http(self) -> httpx.AsyncClient:
        if self._http_client is None:
            # Veo / Vertex auth is `x-goog-api-key`; the operation download
            # URL embeds its own credentials so this header is harmless on
            # the GET side too.
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


def _extract_videos(operation: dict[str, Any]) -> list[Any]:
    response = operation.get("response")
    if not isinstance(response, dict):
        return []
    videos = response.get("videos")
    if isinstance(videos, list):
        return videos
    # Some published responses nest under `generateVideoResponse.generatedSamples`;
    # fold that into the same shape so callers don't branch.
    nested = response.get("generateVideoResponse")
    if isinstance(nested, dict):
        samples = nested.get("generatedSamples")
        if isinstance(samples, list):
            return [s.get("video") if isinstance(s, dict) else s for s in samples]
    return []


def _extract_model_version(operation: dict[str, Any]) -> str | None:
    metadata = operation.get("metadata")
    if isinstance(metadata, dict):
        version = metadata.get("model") or metadata.get("modelVersion")
        if isinstance(version, str):
            return version
    return None


def _extract_duration_seconds(operation: dict[str, Any]) -> float | None:
    response = operation.get("response")
    if not isinstance(response, dict):
        return None
    videos = response.get("videos")
    if isinstance(videos, list) and videos:
        first = videos[0]
        if isinstance(first, dict):
            value = first.get("durationSeconds")
            if isinstance(value, int | float):
                return float(value)
    return None


def _redacted_operation(operation: dict[str, Any]) -> dict[str, Any]:
    """Strip the (potentially huge) base64 video payload out of the
    operation envelope so workers can persist a useful audit record
    without bloating GenerationLog. Only `bytesBase64Encoded` is removed;
    `videoUri`, durations, and other metadata stay.
    """
    redacted: dict[str, Any] = {}
    for key, value in operation.items():
        if key == "response" and isinstance(value, dict):
            redacted[key] = _redact_response(value)
        else:
            redacted[key] = value
    return redacted


def _redact_response(response: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in response.items():
        if key == "videos" and isinstance(value, list):
            out[key] = [_redact_video(v) if isinstance(v, dict) else v for v in value]
        else:
            out[key] = value
    return out


def _redact_video(video: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in video.items() if k != "bytesBase64Encoded"}


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
