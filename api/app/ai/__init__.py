"""AI model client infrastructure (T-014, T-015).

Modules:
- `base`: `AIClient` protocol + `AIGenerationResult`
- `circuit`: Redis-backed per-model circuit breaker
- `errors`: HTTP/OpenAI error → AgentError mapping
- `progress`: `progress_publisher` async context manager
- `gpt_image_2`: GptImage2Client (httpx, retry, circuit-aware)
- `stub`: StubAIClient (returns fixture PNG; toggled via AI_STUB_MODE)
- `factory`: process-wide helper picking real vs stub image client
- `reconciler_client`: gpt-5-mini wrapper for prompt reconciliation (T-015);
  shares retry / breaker / error infra with the image client
"""

from __future__ import annotations
