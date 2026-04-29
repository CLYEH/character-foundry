# T-029: Backend — Veo 3.1 i2v client + circuit breaker + stub

**Status:** TODO
**Sprint:** 3
**Est:** M (2h)
**Depends on:** T-014（共用 ai client 介面 / circuit breaker / stub pattern）
**Related:** T-033（motion generation worker 會用本單產出的 client）

---

## Scope

把 Sprint 2 的 AI client 抽象（`api/app/ai/base.py` + `circuit.py` + `stub.py` + `factory.py`）擴出 Veo 3.1 i2v 用的 concrete client。Phase 1 i2v 走 first/last frame（兩端都送 parent image）強化 identity preservation，**使用者不感知**——只是 backend 內部把 parent image 同時填到 first frame 與 last frame 兩個欄位。

**In scope:**
- 新 `api/app/ai/veo_3_1.py`（mirror `gpt_image_2.py`）：
  - `class VeoClient(AIClient)`：
    - `async def generate_i2v(self, *, image_bytes: bytes, prompt: str, duration_seconds: float | None) -> VeoResult`
    - 內部組 request 時 first frame = last frame = image_bytes（per DECISIONS §3）
    - 回 `VeoResult { video_bytes, duration_ms, model_version, generation_log_payload }`
  - 接 OpenAI compatible HTTP client（沿用 T-014 的 client wrapper）
  - Timeout / retry 走 circuit breaker（`circuit.py` 共用）
  - 失敗 → 拋 `MODEL_TIMEOUT` / `MODEL_UNAVAILABLE` / `MODEL_RATE_LIMIT`
- Stub 模式（`api/app/ai/stub.py` 加 `VeoStub`）：
  - 讀 `_fixtures/` 下小 mp4 fixture 回傳（dev / pytest 用，免燒額度）
  - Fixture 加一支 ~1MB 的 placeholder mp4（可用 ffmpeg 生 1 秒灰底）
- Factory：`get_video_client()` 工廠按 `AI_VIDEO_BACKEND=veo|stub` 選實作（mirror image client factory）
- Env：`VEO_API_KEY`、`VEO_BASE_URL`（optional）、`VEO_MODEL_VERSION`（default `veo-3.1`）寫進 `core/config.py`
- Circuit breaker key：`veo-3.1`（讓 `degraded_services` 端點看得到本 client 的健康狀態）
- 測試：unit 測 stub 回 fixture、HTTP 失敗會打開 breaker、retry 行為

**Not in scope:**
- Motion 業務邏輯（T-033 接）
- `degraded_services` UI 顯示（T-009 已做）
- Veo prompt 模板細節（由 T-033 worker 組）

---

## Planning refs

- `planning/backend/ai-integration.md` — AI client 抽象與 circuit breaker 流程
- `planning/backend/api-shape.md` §5.4 — Motion 端點（理解 client 的呼叫者語義）
- `DECISIONS.md` §3 — Veo 3.1 first/last frame 策略
- T-014 的 `api/app/ai/gpt_image_2.py` 與 `_fixtures/` 結構為實作對照

---

## Acceptance criteria

- [ ] `VeoClient.generate_i2v()` 在 stub 模式下回 fixture mp4 bytes、`duration_ms` 對應 fixture 長度
- [ ] HTTP 模式下 timeout 觸發 retry，超過閾值後 circuit breaker 打開
- [ ] Breaker 打開時下次 call 立即拋 `MODEL_UNAVAILABLE`
- [ ] `GET /v1/meta` 的 `degraded_services` 在 breaker 打開時包含 `service: 'veo-3.1'`（沿用既有 aggregation 邏輯）
- [ ] `pytest api/tests/ai/test_veo_3_1.py` 全綠

---

## Files expected to touch

- `api/app/ai/veo_3_1.py` (new)
- `api/app/ai/stub.py` (edit) — 加 `VeoStub`
- `api/app/ai/factory.py` (edit) — 加 `get_video_client()`
- `api/app/ai/_fixtures/veo_placeholder.mp4` (new) — 1MB stub video
- `api/app/core/config.py` (edit) — Veo env vars
- `api/tests/ai/test_veo_3_1.py` (new)
- `api/tests/ai/_fixtures/` 若需追加

---

## Notes

- Veo API 真正規格 Phase 1 沒簽約使用，client 介面以 OpenAI-compatible / Google-compatible 通用 image+prompt → video 為設計骨架；真實串接時若欄位名不同，client 內部包一層 adapter 即可
- 不要在本單呼叫 storage backend 寫檔——bytes 留給 worker（T-033）決定怎麼存
- Circuit breaker 的 key naming 對齊 `degraded_services` enum：`gpt-image-2` / `veo-3.1` / `reconciler`（per api-shape §5.9）
- Stub fixture mp4 體積要小（1MB 以內，git LFS 不必），Phase 1 反正只測 plumbing
