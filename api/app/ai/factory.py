"""Factories for the AI clients (T-014, T-029).

Workers and routes call `get_image_client(redis)` / `get_video_client(redis)`
rather than constructing the clients themselves, so flipping `AI_STUB_MODE`
between dev and prod is a single env-var toggle. Stub mode is the default;
production explicitly sets `AI_STUB_MODE=false` (see
planning/devops/environment-variables.md §2.2).
"""

from __future__ import annotations

from redis.asyncio import Redis

from app.ai import config
from app.ai.base import AIClient, VideoClient
from app.ai.gpt_image_2 import GptImage2Client
from app.ai.stub import StubAIClient, VeoStub
from app.ai.veo_3_1 import Veo31Client


def get_image_client(redis: Redis, *, force_stub: bool | None = None) -> AIClient:
    """Return the appropriate image client.

    `force_stub` exists so unit tests can pin the mode without monkey-
    patching env vars; production code must not pass it.
    """
    use_stub = force_stub if force_stub is not None else config.stub_mode_enabled()
    if use_stub:
        return StubAIClient()
    return GptImage2Client(redis=redis)


def get_video_client(redis: Redis, *, force_stub: bool | None = None) -> VideoClient:
    """Return the appropriate i2v (video) client.

    Mirrors `get_image_client` — same `AI_STUB_MODE` toggle controls both,
    so dev / CI never charges the Veo account by accident. `force_stub`
    is for unit tests; production code must not pass it.
    """
    use_stub = force_stub if force_stub is not None else config.stub_mode_enabled()
    if use_stub:
        return VeoStub()
    return Veo31Client(redis=redis)
