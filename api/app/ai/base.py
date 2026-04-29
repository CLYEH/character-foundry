"""Public AI-client surface used by workers (T-014, T-029).

`AIClient` / `VideoClient` are `Protocol`s (not ABCs) so the stub and real
clients are duck-typed: callers depend only on the methods listed here.
The narrower fields on `AIGenerationResult` / `VeoResult` match what each
ticket asks for; the planning doc's richer `ImageGenerationResult` /
`VideoGenerationResult` shapes are Sprint 2/3 concerns that downstream
callers can extend later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class AIGenerationResult:
    image_bytes: bytes
    model_version: str
    cost_units: float
    duration_ms: int


@dataclass(frozen=True)
class VeoResult:
    """Output of `VideoClient.generate_i2v` (T-029).

    `duration_ms` is the *video playback* duration, not the call wall-clock
    — workers measure latency themselves when writing GenerationLog. For
    the stub it equals the bundled fixture length; for the real client it
    equals the `duration_seconds` requested (Veo honours it deterministically).

    `generation_log_payload` is an opaque dict the worker (T-033) folds into
    GenerationLog.parameters / raw_response. Keeping it loose here avoids
    coupling the AI layer to the GenerationLog schema; that mapping is the
    worker's job.
    """

    video_bytes: bytes
    model_version: str
    duration_ms: int
    generation_log_payload: dict[str, Any] = field(default_factory=dict)


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


@runtime_checkable
class VideoClient(Protocol):
    """i2v surface (T-029). Phase 1 only needs Veo 3.1.

    `image_bytes` is the parent (Base / Alias) frame. The client is
    responsible for sending it as both first frame *and* last frame to
    Veo — that's the identity-preservation trick decided in DECISIONS §3.
    Callers stay oblivious; they just hand over the parent image and a
    prompt.

    Same resilience contract as `AIClient`: per-model breaker, provider
    error → AgentError mapping, return a fully-populated `VeoResult` on
    success.
    """

    async def generate_i2v(
        self,
        *,
        image_bytes: bytes,
        prompt: str,
        duration_seconds: float | None = None,
    ) -> VeoResult: ...
