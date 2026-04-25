"""ReconcilerClient — gpt-5-mini wrapper for prompt reconciliation (T-015).

Mirrors `GptImage2Client`: per-call timeout, bounded retry on transient
failures, per-model circuit breaker, provider-error → AgentError translation.
The breaker is registered under the service name `reconciler` (not the SKU
`gpt-5-mini`) so /v1/meta surfaces a stable degraded-banner identifier
independent of which model version is currently wired in — `MODELS` in
`app.core.constants` already exposes the SKU separately.

The protocol is asymmetric to `AIClient` on purpose: image generation
returns bytes, the reconciler returns a parsed JSON object. Keeping them
separate prevents callers from conflating the two surfaces.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Protocol, runtime_checkable

import httpx
from redis.asyncio import Redis

from app.ai import config
from app.ai.circuit import CircuitBreaker
from app.ai.errors import (
    map_exception_to_agent_error,
    map_response_to_agent_error,
    parse_retry_after_seconds,
)
from app.core.errors import AgentErrorException

# `reconciler` (not the SKU) so /v1/meta renders the same `service` name
# even after we rotate model versions. Matches the convention exercised in
# tests/routes/test_meta.py (`degraded:reconciler`).
RECONCILER_SERVICE_NAME = "reconciler"


@runtime_checkable
class ReconcilerClient(Protocol):
    """Minimal LLM surface: take system + user prompt, return parsed JSON dict.

    Real (`Gpt5MiniClient`) and stub (`StubReconcilerClient`) implementations
    are duck-typed against this protocol so the reconciler module never
    branches on which one is wired in.

    `client_identity` MUST disambiguate stub vs real (and ideally the model
    SKU within real). The reconciler bakes it into the cache key so that
    flipping `AI_STUB_MODE` — or otherwise rotating the wired client — never
    serves a stale stub entry to a real-model run.
    """

    @property
    def client_identity(self) -> str: ...

    async def call(self, *, system_prompt: str, user_prompt: str) -> dict[str, Any]: ...


class Gpt5MiniClient:
    """Real reconciler client — hits OpenAI Chat Completions in JSON mode.

    Chat Completions (rather than the newer `/v1/responses`) because Phase 1
    only needs a structured JSON reply; chat completions is more widely
    deployed and matches the pattern already used by `GptImage2Client`.
    Switch to `/responses` when we need built-in tools or streamed parts.
    """

    def __init__(
        self,
        redis: Redis,
        *,
        api_key: str | None = None,
        api_base: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
        max_tokens: int | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.redis = redis
        self.api_key = api_key if api_key is not None else config.openai_api_key()
        self.api_base = (api_base or config.openai_api_base()).rstrip("/")
        self.model = model or config.reconciler_model()
        self.timeout_seconds = (
            timeout_seconds if timeout_seconds is not None else config.reconciler_timeout_seconds()
        )
        self.max_retries = (
            max_retries if max_retries is not None else config.reconciler_max_retries()
        )
        self.max_tokens = max_tokens if max_tokens is not None else config.reconciler_max_tokens()
        self._http_client = http_client
        self._owns_client = http_client is None
        self._breaker = CircuitBreaker(RECONCILER_SERVICE_NAME, redis)

    async def aclose(self) -> None:
        if self._http_client is not None and self._owns_client:
            await self._http_client.aclose()
            self._http_client = None

    @property
    def client_identity(self) -> str:
        return f"real:{self.model}"

    async def call(self, *, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0,
            "max_tokens": self.max_tokens,
            "response_format": {"type": "json_object"},
        }
        return await self._call_with_resilience("/chat/completions", body)

    async def _http(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                base_url=self.api_base,
                timeout=httpx.Timeout(self.timeout_seconds),
                headers={"Authorization": f"Bearer {self.api_key or ''}"},
            )
        return self._http_client

    async def _call_with_resilience(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        await self._breaker.raise_if_open()

        last_error: AgentErrorException | None = None
        last_response: httpx.Response | None = None
        attempts = self.max_retries + 1

        for attempt in range(attempts):
            last_error, last_response = await self._attempt_once(path, body)
            if last_error is None:
                assert last_response is not None
                try:
                    parsed = self._parse_chat_json(last_response)
                except AgentErrorException as exc:
                    last_error = exc
                else:
                    await self._breaker.record_success()
                    return parsed
            if not last_error.error.retryable or attempt == attempts - 1:
                break
            await self._sleep_for_retry(attempt, response=last_response)

        # Match GptImage2Client's policy: only retryable errors signal upstream
        # unhealth — non-retryable ones (auth / invalid request / content
        # policy) must NOT contribute to opening the breaker, otherwise five
        # bad prompts in a minute turn into a service-wide outage for valid
        # ones (see ai/test_gpt_image_2.py:test_content_policy_failures_*).
        assert last_error is not None
        if last_error.error.retryable:
            await self._breaker.record_failure()
        raise last_error

    async def _attempt_once(
        self, path: str, body: dict[str, Any]
    ) -> tuple[AgentErrorException | None, httpx.Response | None]:
        try:
            response = await self._send(path, body)
        except httpx.HTTPError as exc:
            return map_exception_to_agent_error(self.model, exc), None
        if response.is_success:
            return None, response
        return map_response_to_agent_error(self.model, response), response

    async def _send(self, path: str, body: dict[str, Any]) -> httpx.Response:
        client = await self._http()
        # Build a fully-qualified URL ourselves (same fix as GptImage2Client):
        # httpx's base_url joining would otherwise drop the `/v1` segment when
        # the relative path starts with a slash.
        url = f"{self.api_base}/{path.lstrip('/')}"
        return await client.post(url, json=body)

    def _parse_chat_json(self, response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise map_exception_to_agent_error(
                self.model, RuntimeError(f"non-JSON success body: {exc}")
            ) from exc

        choices = payload.get("choices") if isinstance(payload, dict) else None
        if not isinstance(choices, list) or not choices:
            raise map_exception_to_agent_error(
                self.model, RuntimeError("response.choices missing or empty")
            )
        first = choices[0]
        if not isinstance(first, dict):
            raise map_exception_to_agent_error(
                self.model, RuntimeError("response.choices[0] not an object")
            )
        message = first.get("message")
        if not isinstance(message, dict):
            raise map_exception_to_agent_error(
                self.model, RuntimeError("response.choices[0].message missing")
            )
        content = message.get("content")
        if not isinstance(content, str):
            raise map_exception_to_agent_error(
                self.model,
                RuntimeError("response.choices[0].message.content not a string"),
            )
        try:
            obj = json.loads(content)
        except ValueError as exc:
            raise map_exception_to_agent_error(
                self.model, RuntimeError(f"chat content was not valid JSON: {exc}")
            ) from exc
        if not isinstance(obj, dict):
            raise map_exception_to_agent_error(
                self.model, RuntimeError("chat JSON content was not an object")
            )
        return obj

    async def _sleep_for_retry(
        self, attempt: int, *, response: httpx.Response | None = None
    ) -> None:
        if response is not None:
            seconds = parse_retry_after_seconds(response.headers.get("Retry-After"))
            if seconds is not None:
                await asyncio.sleep(seconds)
                return
        delay = min(2.0**attempt, 8.0)
        await asyncio.sleep(delay)


class StubReconcilerClient:
    """Fixture-backed reconciler for `AI_STUB_MODE=true`.

    Returns an empty-but-schema-valid output. The image client in stub mode
    also returns a fixture PNG, so prompt fidelity doesn't matter for stub
    runs — what matters is that the reconciler module's composition pipeline
    runs end-to-end without an external dependency.
    """

    MODEL_VERSION = "stub-reconciler-v1"

    @property
    def client_identity(self) -> str:
        return f"stub:{self.MODEL_VERSION}"

    async def call(
        self,
        *,
        system_prompt: str,  # noqa: ARG002
        user_prompt: str,  # noqa: ARG002
    ) -> dict[str, Any]:
        return {"reconciled_note_en": "", "removed_segments": []}


def get_reconciler_client(redis: Redis, *, force_stub: bool | None = None) -> ReconcilerClient:
    """Pick the real or stub reconciler client based on `AI_STUB_MODE`.

    `force_stub` exists for tests that want to pin the mode without env-var
    monkeypatching; production must not pass it.
    """
    use_stub = force_stub if force_stub is not None else config.stub_mode_enabled()
    if use_stub:
        return StubReconcilerClient()
    return Gpt5MiniClient(redis=redis)
