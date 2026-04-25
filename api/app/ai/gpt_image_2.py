"""GptImage2Client — real provider client for gpt-image-2 (T-014).

Wraps OpenAI's `/v1/images/generations` and `/v1/images/edits` endpoints
through `httpx.AsyncClient`, layering:

  1. Per-attempt timeout (`GPT_IMAGE_2_TIMEOUT_MS`)
  2. Bounded retry with exponential backoff for transient failures
     (timeout / 5xx / 429); 4xx fails fast
  3. Per-model circuit breaker (`circuit:gpt-image-2`)
  4. Provider error → AgentError translation (`app.ai.errors`)

The retry loop counts a failed *call* (after exhausting retries) as a single
breaker failure — not each attempt. Otherwise one flaky call would burn
the budget in seconds. See planning/backend/ai-integration.md §3.3 / §3.4.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from typing import Any

import httpx
from redis.asyncio import Redis

from app.ai import config
from app.ai.base import AIGenerationResult
from app.ai.circuit import CircuitBreaker
from app.ai.errors import (
    map_exception_to_agent_error,
    map_response_to_agent_error,
    parse_retry_after_seconds,
)
from app.core.errors import AgentErrorException

_logger = logging.getLogger(__name__)

# Aspect ratio → OpenAI `size` string. The provider only accepts a fixed
# enum, so we map our internal ratios down to the closest supported size.
# Phase 1 needs portrait (2:3) and square (1:1); add more here as new
# motion / alias inputs arrive.
_SIZE_MAP: dict[str, str] = {
    "1:1": "1024x1024",
    "2:3": "1024x1536",
    "3:2": "1536x1024",
    "9:16": "1024x1792",
    "16:9": "1792x1024",
}


class GptImage2Client:
    """Implements `app.ai.base.AIClient` against the OpenAI Images API."""

    def __init__(
        self,
        redis: Redis,
        *,
        api_key: str | None = None,
        api_base: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.redis = redis
        self.api_key = api_key if api_key is not None else config.openai_api_key()
        self.api_base = (api_base or config.openai_api_base()).rstrip("/")
        self.model = model or config.gpt_image_2_model()
        self.timeout_seconds = (
            timeout_seconds if timeout_seconds is not None else config.gpt_image_2_timeout_seconds()
        )
        self.max_retries = (
            max_retries if max_retries is not None else config.gpt_image_2_max_retries()
        )
        self._http_client = http_client
        self._owns_client = http_client is None
        self._breaker = CircuitBreaker(self.model, redis)

    async def aclose(self) -> None:
        if self._http_client is not None and self._owns_client:
            await self._http_client.aclose()
            self._http_client = None

    # ------------------------------------------------------------------ public

    async def generate_image_text2image(
        self,
        prompt: str,
        *,
        aspect_ratio: str = "1:1",
        seed: int | None = None,
    ) -> AIGenerationResult:
        json_body: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "size": self._size_for(aspect_ratio),
            "n": 1,
            "response_format": "b64_json",
            "quality": "hd",
        }
        if seed is not None:
            json_body["seed"] = seed
        return await self._call_with_resilience("/images/generations", json_body=json_body)

    async def generate_image_image2image(
        self,
        prompt: str,
        image: bytes,
        *,
        aspect_ratio: str = "1:1",
        seed: int | None = None,
    ) -> AIGenerationResult:
        files: dict[str, tuple[str, bytes, str]] = {
            "image": ("image.png", image, "image/png"),
        }
        data: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "size": self._size_for(aspect_ratio),
            "n": "1",
            "response_format": "b64_json",
        }
        if seed is not None:
            data["seed"] = str(seed)
        return await self._call_with_resilience("/images/edits", form_data=data, files=files)

    async def generate_image_inpaint(
        self,
        prompt: str,
        image: bytes,
        mask: bytes,
        *,
        aspect_ratio: str = "1:1",
        seed: int | None = None,
    ) -> AIGenerationResult:
        files: dict[str, tuple[str, bytes, str]] = {
            "image": ("image.png", image, "image/png"),
            "mask": ("mask.png", mask, "image/png"),
        }
        data: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "size": self._size_for(aspect_ratio),
            "n": "1",
            "response_format": "b64_json",
        }
        if seed is not None:
            data["seed"] = str(seed)
        return await self._call_with_resilience("/images/edits", form_data=data, files=files)

    # ----------------------------------------------------------------- private

    def _size_for(self, aspect_ratio: str) -> str:
        return _SIZE_MAP.get(aspect_ratio, _SIZE_MAP["1:1"])

    async def _http(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                base_url=self.api_base,
                timeout=httpx.Timeout(self.timeout_seconds),
                headers={"Authorization": f"Bearer {self.api_key or ''}"},
            )
        return self._http_client

    async def _call_with_resilience(
        self,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        form_data: dict[str, Any] | None = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
    ) -> AIGenerationResult:
        await self._breaker.raise_if_open()

        last_error: AgentErrorException | None = None
        last_response: httpx.Response | None = None
        attempts = self.max_retries + 1
        started = time.perf_counter()

        for attempt in range(attempts):
            last_error, last_response = await self._attempt_once(
                path, json_body=json_body, form_data=form_data, files=files
            )

            # Success path: parse → record_success → return. A bad payload
            # body still counts as one breaker failure (mapped below) — the
            # provider answered 2xx but with junk we can't use.
            if last_error is None:
                assert last_response is not None
                duration_ms = int((time.perf_counter() - started) * 1000)
                try:
                    result = self._parse_success(last_response, duration_ms=duration_ms)
                except AgentErrorException as exc:
                    last_error = exc
                else:
                    await self._breaker.record_success()
                    return result

            if not last_error.error.retryable or attempt == attempts - 1:
                break
            await self._sleep_for_retry(attempt, response=last_response)

        # Exhausted retries (or fast-failed). Count this as ONE breaker
        # failure regardless of attempt count; otherwise a single flaky
        # call could trip the breaker on its own.
        #
        # Codex P1 round-1: only *retryable* errors signal upstream
        # unhealth — they're the timeout / 5xx / 429 / transport family.
        # Non-retryable errors (PROMPT_CONTENT_POLICY, MODEL_INVALID_REQUEST,
        # INTERNAL_AUTH_FAILED) are user-input or config issues and must
        # NOT contribute to opening the breaker; otherwise five bad prompts
        # in a minute turn into a service-wide outage for the next 5 min.
        assert last_error is not None  # set by every break path above
        if last_error.error.retryable:
            await self._breaker.record_failure()
        raise last_error

    async def _attempt_once(
        self,
        path: str,
        *,
        json_body: dict[str, Any] | None,
        form_data: dict[str, Any] | None,
        files: dict[str, tuple[str, bytes, str]] | None,
    ) -> tuple[AgentErrorException | None, httpx.Response | None]:
        try:
            response = await self._send(path, json_body=json_body, form_data=form_data, files=files)
        except httpx.HTTPError as exc:
            return map_exception_to_agent_error(self.model, exc), None
        if response.is_success:
            return None, response
        return map_response_to_agent_error(self.model, response), response

    async def _send(
        self,
        path: str,
        *,
        json_body: dict[str, Any] | None,
        form_data: dict[str, Any] | None,
        files: dict[str, tuple[str, bytes, str]] | None,
    ) -> httpx.Response:
        client = await self._http()
        # Codex P1 round-1: build a fully-qualified URL ourselves rather than
        # relying on httpx's base_url joining. With base_url=".../v1" and an
        # absolute path like "/images/generations", RFC 3986 path resolution
        # drops the `/v1` segment and posts to ".../images/...". Production
        # hits the wrong endpoint; stub mode hides it because MockTransport
        # accepts any URL. Concatenate explicitly so the path is never re-rooted.
        url = f"{self.api_base}/{path.lstrip('/')}"
        if files is not None:
            return await client.post(url, data=form_data or {}, files=files)
        return await client.post(url, json=json_body)

    def _parse_success(self, response: httpx.Response, *, duration_ms: int) -> AIGenerationResult:
        try:
            payload = response.json()
        except ValueError as exc:
            raise map_exception_to_agent_error(
                self.model, RuntimeError(f"non-JSON success body: {exc}")
            ) from exc

        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list) or not data:
            raise map_exception_to_agent_error(
                self.model, RuntimeError("response.data missing or empty")
            )
        first = data[0]
        if not isinstance(first, dict) or "b64_json" not in first:
            raise map_exception_to_agent_error(
                self.model, RuntimeError("response.data[0].b64_json missing")
            )
        try:
            image_bytes = base64.b64decode(first["b64_json"])
        except (ValueError, TypeError) as exc:
            raise map_exception_to_agent_error(
                self.model, RuntimeError(f"b64 decode failed: {exc}")
            ) from exc

        # cost_units: hd = 1.0, standard = 0.5 (planning §6).
        cost = 1.0
        return AIGenerationResult(
            image_bytes=image_bytes,
            model_version=str(payload.get("model") or self.model),
            cost_units=cost,
            duration_ms=duration_ms,
        )

    async def _sleep_for_retry(
        self, attempt: int, *, response: httpx.Response | None = None
    ) -> None:
        # Honour Retry-After if the server told us how long to wait.
        # `parse_retry_after_seconds` accepts both the delta-seconds and
        # the HTTP-date forms allowed by RFC 9110 §10.2.3 (Codex P2 round-3).
        if response is not None:
            seconds = parse_retry_after_seconds(response.headers.get("Retry-After"))
            if seconds is not None:
                await asyncio.sleep(seconds)
                return
        delay = min(2.0**attempt, 8.0)
        await asyncio.sleep(delay)
