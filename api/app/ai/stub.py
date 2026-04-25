"""StubAIClient — fixture-backed AI client for dev / CI / E2E (T-014).

Loads a 512x768 transparent PNG from `app/ai/_fixtures/sample_base.png`
and returns it for every call. The same bytes are used for text2image,
image2image, and inpaint — Phase 1 callers only need *some* valid PNG so
downstream pipeline (storage, thumbnail, manifest) can be exercised.

Sleep duration mimics a real call so SSE progress bars have something
to animate against (`StubAIClient.sleep_seconds`).
"""

from __future__ import annotations

import asyncio
from importlib import resources
from typing import Final

from app.ai.base import AIGenerationResult

_FIXTURE_PACKAGE = "app.ai._fixtures"
_FIXTURE_NAME = "sample_base.png"


def _load_sample_bytes() -> bytes:
    """Read the bundled stub PNG. Cached lazily by the calling module."""
    return resources.files(_FIXTURE_PACKAGE).joinpath(_FIXTURE_NAME).read_bytes()


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
