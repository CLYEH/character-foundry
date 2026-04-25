"""Public AI-client surface used by workers (T-014).

`AIClient` is a `Protocol` (not an ABC) so the stub and the real client are
duck-typed: callers depend only on the three `generate_image_*` methods
listed below. The narrower fields on `AIGenerationResult` match what the
ticket asks for (`image_bytes`, `model_version`, `cost_units`,
`duration_ms`); the planning doc's richer `ImageGenerationResult` shape
is a Sprint 2/3 concern that downstream callers can extend later.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class AIGenerationResult:
    image_bytes: bytes
    model_version: str
    cost_units: float
    duration_ms: int


@runtime_checkable
class AIClient(Protocol):
    """Image-generation surface common to real + stub clients.

    Only the three modes Phase 1 needs (text2image / image2image / inpaint)
    are exposed. Each call must:
      - Resolve cooperatively against the per-model circuit breaker
        (raise `MODEL_UNAVAILABLE` when OPEN, do not call out)
      - Map provider errors onto `AgentError` codes from api-shape.md §4.1
      - Return a fully-populated `AIGenerationResult` on success
    """

    async def generate_image_text2image(
        self,
        prompt: str,
        *,
        aspect_ratio: str = "1:1",
        seed: int | None = None,
    ) -> AIGenerationResult: ...

    async def generate_image_image2image(
        self,
        prompt: str,
        image: bytes,
        *,
        aspect_ratio: str = "1:1",
        seed: int | None = None,
    ) -> AIGenerationResult: ...

    async def generate_image_inpaint(
        self,
        prompt: str,
        image: bytes,
        mask: bytes,
        *,
        aspect_ratio: str = "1:1",
        seed: int | None = None,
    ) -> AIGenerationResult: ...
