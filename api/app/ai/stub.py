"""StubAIClient / VeoStub — fixture-backed AI clients for dev / CI / E2E (T-014, T-029).

`StubAIClient` loads a 512x768 transparent PNG from
`app/ai/_fixtures/sample_base.png` and returns it for every call.

`VeoStub` loads a tiny placeholder mp4 from `app/ai/_fixtures/veo_placeholder.mp4`
and returns it for every `generate_i2v` call. Same bytes regardless of input
— Phase 1 callers only need *some* valid mp4 so downstream pipeline
(storage, manifest, motion delivery) can be exercised.

Sleep duration mimics a real call so SSE progress bars have something
to animate against (`*.sleep_seconds`).
"""

from __future__ import annotations

import asyncio
from importlib import resources
from typing import Any, Final

from app.ai.base import AIGenerationResult, VeoResult

_FIXTURE_PACKAGE = "app.ai._fixtures"
_FIXTURE_NAME = "sample_base.png"
_VEO_FIXTURE_NAME = "veo_placeholder.mp4"


def _load_sample_bytes() -> bytes:
    """Read the bundled stub PNG. Cached lazily by the calling module."""
    return resources.files(_FIXTURE_PACKAGE).joinpath(_FIXTURE_NAME).read_bytes()


def _load_veo_fixture_bytes() -> bytes:
    """Read the bundled stub mp4. Lazy-loaded once per VeoStub instance."""
    return resources.files(_FIXTURE_PACKAGE).joinpath(_VEO_FIXTURE_NAME).read_bytes()


class StubAIClient:
    """Returns the bundled stub PNG for every method on the `AIClient` protocol.

    Tests should construct directly; the factory in `app.ai.factory` handles
    swapping based on `AI_STUB_MODE`.
    """

    MODEL_VERSION: Final[str] = "stub-v1"
    DEFAULT_DURATION_MS: Final[int] = 2000
    DEFAULT_COST_UNITS: Final[float] = 0.0

    def __init__(self, *, sleep_seconds: float = 0.0) -> None:
        # Loaded once per instance — the file is small (~3KB) and tests want
        # the bytes to be stable across calls.
        self._image_bytes = _load_sample_bytes()
        self.sleep_seconds = sleep_seconds

    @property
    def image_bytes(self) -> bytes:
        return self._image_bytes

    async def _result(self) -> AIGenerationResult:
        if self.sleep_seconds > 0:
            await asyncio.sleep(self.sleep_seconds)
        return AIGenerationResult(
            image_bytes=self._image_bytes,
            model_version=self.MODEL_VERSION,
            cost_units=self.DEFAULT_COST_UNITS,
            duration_ms=self.DEFAULT_DURATION_MS,
        )

    async def generate_image_text2image(
        self,
        prompt: str,  # noqa: ARG002
        *,
        aspect_ratio: str = "1:1",  # noqa: ARG002
        seed: int | None = None,  # noqa: ARG002
    ) -> AIGenerationResult:
        return await self._result()

    async def generate_image_image2image(
        self,
        prompt: str,  # noqa: ARG002
        image: bytes,  # noqa: ARG002
        *,
        aspect_ratio: str = "1:1",  # noqa: ARG002
        seed: int | None = None,  # noqa: ARG002
    ) -> AIGenerationResult:
        return await self._result()

    async def generate_image_inpaint(
        self,
        prompt: str,  # noqa: ARG002
        image: bytes,  # noqa: ARG002
        mask: bytes,  # noqa: ARG002
        *,
        aspect_ratio: str = "1:1",  # noqa: ARG002
        seed: int | None = None,  # noqa: ARG002
    ) -> AIGenerationResult:
        return await self._result()


class VeoStub:
    """Returns the bundled placeholder mp4 for every `generate_i2v` call.

    Implements `app.ai.base.VideoClient` by duck-typing. The stub never
    contacts a real provider, so it ignores the per-model circuit breaker
    entirely — callers that explicitly want breaker behaviour use the real
    `Veo31Client`.

    `duration_ms` defaults to 1000ms (matches the bundled fixture's nominal
    1-second placeholder); callers passing `duration_seconds` get that value
    echoed back so the GenerationLog still shows what was requested.
    """

    MODEL_VERSION: Final[str] = "stub-veo-v1"
    FIXTURE_DURATION_MS: Final[int] = 1000

    def __init__(self, *, sleep_seconds: float = 0.0) -> None:
        # Bytes are tiny (~56B) but cached per-instance so tests can compare
        # by identity if they want to.
        self._video_bytes = _load_veo_fixture_bytes()
        self.sleep_seconds = sleep_seconds

    @property
    def video_bytes(self) -> bytes:
        return self._video_bytes

    async def generate_i2v(
        self,
        *,
        image_bytes: bytes,  # noqa: ARG002
        prompt: str,  # noqa: ARG002
        duration_seconds: float | None = None,
    ) -> VeoResult:
        if self.sleep_seconds > 0:
            await asyncio.sleep(self.sleep_seconds)
        if duration_seconds is not None:
            duration_ms = int(duration_seconds * 1000)
        else:
            duration_ms = self.FIXTURE_DURATION_MS
        payload: dict[str, Any] = {
            "model": self.MODEL_VERSION,
            "duration_seconds": duration_seconds,
            "stub": True,
        }
        return VeoResult(
            video_bytes=self._video_bytes,
            model_version=self.MODEL_VERSION,
            duration_ms=duration_ms,
            generation_log_payload=payload,
        )
