"""AI model client infrastructure (T-014).

Modules:
- `base`: `AIClient` protocol + `AIGenerationResult`
- `circuit`: Redis-backed per-model circuit breaker
- `errors`: HTTP/OpenAI error → AgentError mapping
- `progress`: `progress_publisher` async context manager
- `gpt_image_2`: GptImage2Client (httpx, retry, circuit-aware)
- `stub`: StubAIClient (returns fixture PNG; toggled via AI_STUB_MODE)
- `factory`: process-wide helper picking real vs stub client
"""

from __future__ import annotations
