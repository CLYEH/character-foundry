# T-014: Backend — AI client infra (gpt-image-2 + circuit breaker + stub)

**Status:** TODO
**Sprint:** 2
**Est:** M (2h)
**Depends on:** T-009 (`/v1/meta degraded_services` schema)
**Related:** T-015（reconciler client 複用本單 infra）、T-017（checkpoint worker 使用）

---

## Scope

建立 AI model client 抽象層 + gpt-image-2 具體 client + circuit breaker 整合 `/v1/meta.degraded_services`。提供 stub mode 讓後續開發與 E2E 不花 API 費用。

**In scope:**
- `AIClient` protocol（`generate_image_text2image`、`generate_image_image2image`、`generate_image_inpaint`；每個回 `AIGenerationResult { image_bytes, model_version, cost_units, duration_ms }`）
- `GptImage2Client` 實作 — 包 OpenAI SDK；retry（指數退避 3 次）；timeout 60s；AgentError mapping（`MODEL_TIMEOUT` / `MODEL_RATE_LIMIT` / `PROMPT_CONTENT_POLICY` / `MODEL_UNAVAILABLE`，對齊 `api-shape.md` §4.1 — content policy 歸 `PROMPT_`）
- `StubAIClient` — 固定回本機內建 sample PNG（存 `api/tests/fixtures/sample_*.png`），`AI_STUB_MODE=true` 時自動切換
- Circuit breaker（per-model）：
  - 連續失敗 ≥ 5 次（1 分鐘 window 內） → OPEN，retry_at = now + 300s（對齊 `planning/backend/ai-integration.md` §3.4 `open_duration_seconds = 300`）
  - OPEN 期間新呼叫直接 raise `MODEL_UNAVAILABLE`
  - 狀態存 Redis（`circuit:{model}`），讓 `/v1/meta` 能讀到
- Update `/v1/meta.degraded_services`：聚合 Redis circuit 狀態（T-009 已定 schema，本單是填資料）
- Progress estimator loop helper（T-013 預留 schema）：worker 呼叫 `async with progress_publisher(task_id, estimated_ms):` 期間每 2s publish 一筆 `running` event
- 單元測試：retry、timeout、circuit open/close、stub mode、error mapping

**Not in scope:**
- Veo 3.1 client（Sprint 3+）
- 真的 prompt reconciler 邏輯（T-015）
- Per-user rate limit（Phase 1 用 quota 顯示即可）

---

## Planning refs

- `planning/backend/ai-integration.md` — client 契約、retry / timeout / circuit breaker
- `planning/backend/api-shape.md` §4.1, §5.9 — AgentError code、degraded_services schema
- `DECISIONS.md` §3 — gpt-image-2 as image model

---

## Acceptance criteria

- [ ] `GptImage2Client.generate_image_text2image("a cat", constraints=...)` → 回 `AIGenerationResult` with PNG bytes
- [ ] `AI_STUB_MODE=true` 時 client 回 stub sample，不呼叫外部 API
- [ ] 連續 5 次失敗 → 第 6 次直接 raise `MODEL_UNAVAILABLE`，`/v1/meta.degraded_services` 出現該 service
- [ ] Circuit retry_at 到期後，下一次呼叫重試並在成功後 close circuit
- [ ] Error mapping：429 → `MODEL_RATE_LIMIT`（retryable=true）；content policy → `PROMPT_CONTENT_POLICY`；timeout → `MODEL_TIMEOUT`
- [ ] Progress publisher loop 在退出 context 時 cancel 乾淨（無 leaked task）
- [ ] `pytest api/tests/ai/` 全綠

---

## Files expected to touch

- `api/app/ai/base.py` (new) — `AIClient` protocol + `AIGenerationResult` dataclass
- `api/app/ai/gpt_image_2.py` (new)
- `api/app/ai/stub.py` (new)
- `api/app/ai/circuit.py` (new) — Redis-backed breaker
- `api/app/ai/errors.py` (new) — OpenAI → AgentError mapping
- `api/app/ai/progress.py` (new) — `progress_publisher` async CM
- `api/app/routers/meta.py` (edit) — degraded_services 改為從 Redis 讀
- `api/app/core/settings.py` (edit) — `OPENAI_API_KEY`、`AI_STUB_MODE`
- `api/tests/ai/` (new)
- `api/tests/fixtures/sample_base.png` (new) — 透明背景 512x768 stub
- `planning/devops/environment-variables.md` (edit) — `OPENAI_API_KEY`、`AI_STUB_MODE`

---

## Notes

- `AI_STUB_MODE=true` 是 dev / CI 預設，production 才設 false
- Circuit breaker key 用模型 ID（`gpt-image-2`、`veo-3.1`、`reconciler`）對齊 `/v1/meta` service 名稱
- Stub 產生 PNG 時仍吐一個合理的 `duration_ms`（e.g. 2000）讓 UI 的 progress bar 有東西動
- 不要在 client 層做 prompt injection；prompt 組合是 reconciler（T-015）的事
- Retry policy：5xx / 429 / timeout → retry；4xx（除 429）直接 raise，不 retry
