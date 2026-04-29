"""StubAIClient / VeoStub — fixture-backed AI clients for dev / CI / E2E (T-014, T-029, T-030).

`StubAIClient` loads bundled 512x768 PNG fixtures from `app/ai/_fixtures/`
and returns them per-mode so dev / CI / E2E exercise the AI pipeline
without paying the provider:

  - text2image / image2image (Sprint 2 checkpoint flows) → `sample_base.png`
  - edit_image2image (Sprint 3 alias)                    → `edit_sample.png`
  - edit_inpaint     (Sprint 3 alias)                    → `inpaint_sample.png`

Per-mode fixtures let downstream tests assert which path actually fired
(byte equality), instead of relying on filename or call introspection.

`VeoStub` (T-029) loads a tiny placeholder mp4 from
`app/ai/_fixtures/veo_placeholder.mp4` and returns it for every
`generate_i2v` call. Same bytes regardless of input — Phase 1 callers
only need *some* valid mp4 so downstream pipeline (storage, manifest,
motion delivery) can be exercised.

Sleep duration mimics a real call so SSE progress bars have something
to animate against (`*.sleep_seconds`).
"""

from __future__ import annotations

import asyncio
from importlib import resources
from typing import Any, Final

from app.ai.base import AIGenerationResult, VeoResult
from app.ai.mask import validate_inpaint_mask

_FIXTURE_PACKAGE = "app.ai._fixtures"
_BASE_FIXTURE = "sample_base.png"
_EDIT_FIXTURE = "edit_sample.png"
_INPAINT_FIXTURE = "inpaint_sample.png"
_VEO_FIXTURE_NAME = "veo_placeholder.mp4"


def _load_fixture(name: str) -> bytes:
    return resources.files(_FIXTURE_PACKAGE).joinpath(name).read_bytes()


class StubAIClient:
    """Returns bundled stub PNGs for every method on the `AIClient` protocol.

    Tests should construct directly; the factory in `app.ai.factory` handles
    swapping based on `AI_STUB_MODE`.
    """

    MODEL_VERSION: Final[str] = "stub-v1"
    DEFAULT_DURATION_MS: Final[int] = 2000
    DEFAULT_COST_UNITS: Final[float] = 0.0

    def __init__(self, *, sleep_seconds: float = 0.0) -> None:
        # Loaded once per instance — files are small (~1.6KB each) and
        # tests want the bytes to be stable across calls.
        self._base_bytes = _load_fixture(_BASE_FIXTURE)
        self._edit_bytes = _load_fixture(_EDIT_FIXTURE)
        self._inpaint_bytes = _load_fixture(_INPAINT_FIXTURE)
        self.sleep_seconds = sleep_seconds

    @property
    def image_bytes(self) -> bytes:
        return self._base_bytes

    @property
    def edit_image_bytes(self) -> bytes:
        return self._edit_bytes

    @property
    def inpaint_image_bytes(self) -> bytes:
        return self._inpaint_bytes

    async def _result(self, payload: bytes) -> AIGenerationResult:
        if self.sleep_seconds > 0:
            await asyncio.sleep(self.sleep_seconds)
        return AIGenerationResult(
            image_bytes=payload,
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
        return await self._result(self._base_bytes)

    async def generate_image_image2image(
        self,
        prompt: str,  # noqa: ARG002
        image: bytes,  # noqa: ARG002
        *,
        aspect_ratio: str = "1:1",  # noqa: ARG002
        seed: int | None = None,  # noqa: ARG002
    ) -> AIGenerationResult:
        return await self._result(self._base_bytes)

    async def generate_image_inpaint(
        self,
        prompt: str,  # noqa: ARG002
        image: bytes,  # noqa: ARG002
        mask: bytes,  # noqa: ARG002
        *,
        aspect_ratio: str = "1:1",  # noqa: ARG002
        seed: int | None = None,  # noqa: ARG002
    ) -> AIGenerationResult:
        return await self._result(self._base_bytes)

    async def edit_image2image(
        self,
        *,
        base_image_bytes: bytes,  # noqa: ARG002
        reference_image_bytes: list[bytes] | None,  # noqa: ARG002
        prompt: str,  # noqa: ARG002
    ) -> AIGenerationResult:
        return await self._result(self._edit_bytes)

    async def edit_inpaint(
        self,
        *,
        base_image_bytes: bytes,
        mask_png_bytes: bytes,
        prompt: str,  # noqa: ARG002
    ) -> AIGenerationResult:
        # Mirror the real client: reject malformed masks here too so
        # tests / E2E using stub mode catch mask bugs that would
        # otherwise only surface in production.
        validate_inpaint_mask(base_image_bytes, mask_png_bytes)
        return await self._result(self._inpaint_bytes)


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
        self._video_bytes = _load_fixture(_VEO_FIXTURE_NAME)
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
        from app.ai.veo_3_1 import _clamp_duration_seconds

        if self.sleep_seconds > 0:
            await asyncio.sleep(self.sleep_seconds)
        if duration_seconds is not None:
            # Mirror the real client's clamp so callers see consistent
            # `duration_ms` regardless of stub vs real mode (Codex P1 round-6).
            clamped = _clamp_duration_seconds(duration_seconds)
            duration_ms = int(clamped * 1000)
        else:
            clamped = None
            duration_ms = self.FIXTURE_DURATION_MS
        payload: dict[str, Any] = {
            "model": self.MODEL_VERSION,
            "duration_seconds": clamped,
            "stub": True,
        }
        return VeoResult(
            video_bytes=self._video_bytes,
            model_version=self.MODEL_VERSION,
            duration_ms=duration_ms,
            generation_log_payload=payload,
        )
