"""AI model client infrastructure (T-014, T-015, T-029).

Modules:
- `base`: `AIClient` / `VideoClient` protocols + `AIGenerationResult` / `VeoResult`
- `circuit`: Redis-backed per-model circuit breaker
- `errors`: HTTP/OpenAI/Veo error → AgentError mapping
- `progress`: `progress_publisher` async context manager
- `gpt_image_2`: GptImage2Client (httpx, retry, circuit-aware)
- `veo_3_1`: Veo31Client — i2v long-running operation client (httpx, retry,
  circuit-aware); first-frame == last-frame identity-anchor trick
- `stub`: StubAIClient + VeoStub (return fixture bytes; toggled via AI_STUB_MODE)
- `factory`: process-wide helpers picking real vs stub image / video client
- `reconciler_client`: gpt-5-mini wrapper for prompt reconciliation (T-015);
  shares retry / breaker / error infra with the image client
"""

from __future__ import annotations
