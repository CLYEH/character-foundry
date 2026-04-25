"""Factory for the image-generation client (T-014).

Workers and routes call `get_image_client(redis)` rather than constructing
the client themselves, so flipping `AI_STUB_MODE` between dev and prod is
a single env-var toggle. Stub mode is the default; production explicitly
sets `AI_STUB_MODE=false` (see planning/devops/environment-variables.md
§2.2).
"""

from __future__ import annotations

from redis.asyncio import Redis

from app.ai import config
from app.ai.base import AIClient
from app.ai.gpt_image_2 import GptImage2Client
from app.ai.stub import StubAIClient


def get_image_client(redis: Redis, *, force_stub: bool | None = None) -> AIClient:
    """Return the appropriate image client.

    `force_stub` exists so unit tests can pin the mode without monkey-
    patching env vars; production code must not pass it.
    """
    use_stub = force_stub if force_stub is not None else config.stub_mode_enabled()
    if use_stub:
        return StubAIClient()
    return GptImage2Client(redis=redis)
