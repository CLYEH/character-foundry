# T-009: Backend /health + /v1/meta

**Status:** DONE
**Sprint:** 1
**Est:** S (1h)
**Depends on:** T-001, T-002
**Related:** T-010（DegradedBanner 讀 meta）

---

## Scope

從 T-001 的佔位 `/health` 升級為真的檢查 + 建 `/v1/meta` 給 Frontend 拿平台資訊。

**In scope:**
- `/health`（不帶 `/v1` 前綴）：實際檢查 DB ping + Redis ping + storage 可寫
  回 `{ status: 'ok' | 'degraded', db: 'ok'|'fail', redis: 'ok'|'fail', storage: 'ok'|'fail' }`
  全好 200，任一壞 503
- `/v1/meta`：回 `{ models, preset_motions, platform_constraints_version, api_version, degraded_services }`
  - `degraded_services` 讀 Redis key pattern `degraded:*`（Phase 1 可能都是空，但介面做好）
- 定義平台 constraint version（放 config file）
- Preset motion 清單（5 個）放 constants
- Unit tests

**Not in scope:**
- Circuit breaker 實作（T-xxx AI integration 單才做；本單 degraded_services 只讀空陣列）
- Prometheus `/metrics`（之後 sprint）

---

## Planning refs

- `planning/backend/api-shape.md` §5.9 meta endpoint（已更新含 degraded_services）
- `planning/backend/ai-integration.md` §3.5 Degraded state 聚合機制（本單先做讀取，寫入等 AI client 做時）
- `planning/backend/prompt-reconciler.md` §3 platform_constraints.yaml 版本
- FB-1 resolved：degraded_services 的 schema

---

## Acceptance criteria

- [ ] `curl /health` 回 200 + `{status:'ok', db:'ok', redis:'ok', storage:'ok'}`
- [ ] 斷開 Redis → `/health` 回 503 + `redis:'fail'`
- [ ] `curl /v1/meta` 無需 auth，回 payload 含 5 個預設 motion、constraint version `v1`、`degraded_services: []`
- [ ] Set `redis-cli SET degraded:gpt-image-2 '{"reason":"CIRCUIT_OPEN","retry_at":"2026-04-23T11:00:00Z","message":"暫停 5 分鐘"}'` 後，`/v1/meta.degraded_services` 正確回傳該陣列
- [ ] `pytest api/tests/routes/test_meta.py` 綠

---

## Files expected to touch

- `api/app/api/routes/health.py` (edit or new)
- `api/app/api/routes/meta.py` (new)
- `api/app/core/constants.py` (new) — `PRESET_MOTIONS`, `API_VERSION`
- `api/app/core/platform_constraints.py` (new) — load `platform_constraints.yaml`, expose version
- `api/platform_constraints.yaml` (new) — 依 prompt-reconciler.md §3
- `api/app/services/degraded_services.py` (new) — Redis key 聚合讀取
- `api/tests/routes/test_health.py` (new)
- `api/tests/routes/test_meta.py` (new)

---

## Notes

- Storage check 用 `storage.exists('/health-probe')`（放個 marker 檔在 setup 時就好）
- `/v1/meta` 一分鐘輕量 cache（Redis）避免被 Frontend 打太頻繁
- `preset_motions` schema：`[{type, display_name_zh, display_name_en, default_duration_ms}]`
- `api_version` hardcode `'v1'`（改版會用新路由，不改此欄）
- Degraded service 讀取用 `SCAN` 不是 `KEYS`（避免 prod blocking）
