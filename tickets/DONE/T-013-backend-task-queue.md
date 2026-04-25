# T-013: Backend — Task queue (arq + Redis) + Task API

**Status:** TODO
**Sprint:** 2
**Est:** M (2h)
**Depends on:** T-003 (tasks migration), T-006 (auth)
**Related:** T-014, T-017, T-018（所有非同步 AI 生成）

---

## Scope

建立非同步任務骨架：arq worker + Redis broker + `tasks` 表 repository + `/v1/tasks/*` endpoints（GET、SSE、cancel）。這是 Sprint 2 所有 AI 生成（Checkpoint / Alias / Motion）的底座。

**In scope:**
- arq worker 進程（`api/app/workers/arq_worker.py`），與 FastAPI 在 docker-compose 分開跑
- Redis 連線設定（`REDIS_URL` env var）+ arq settings
- `TaskRepository` / `TaskService`（create、get、list、update、cancel-request）
- Redis pub/sub helper（`task:{id}` channel）供 worker 推進度、SSE 讀取
- `estimate_duration()` 函式（p50 歷史 or hardcoded default per task_type）
- Endpoint：
  - `GET /v1/tasks/{task_id}` — 回 Task DTO
  - `GET /v1/tasks/{task_id}/stream` — SSE；先推 initial state，再 subscribe Redis channel，終止狀態後 close
  - `POST /v1/tasks/{task_id}/cancel` — 回 `{ task, cancel_outcome }`，4 種 outcome 見 API spec
  - `GET /v1/tasks` — 本人 task 清單（?status=running、?limit）
- Scheduled cleanup job（arq cron）：刪除 `completed/failed/cancelled` 且 `completed_at < NOW() - 24h`
- 一個 dummy worker `run_noop` 用來驗證 queue 通路（Sprint 2 後續 ticket 再加真的 handler）
- 單元測試：state machine、cancel outcomes 四種情境、SSE initial-state short-circuit
- Worker / Redis 加進 `docker-compose.yml`

**Not in scope:**
- 真的 gpt-image-2 / Veo 呼叫（T-014）
- Webhook 通知（Phase 1 暫緩到 Sprint 5）
- Queue position 精算（先用 `COUNT(*) WHERE status='queued' AND queued_at < this.queued_at`）

---

## Planning refs

- `planning/backend/task-queue.md` §1–§5 — arq 選型、schema、SSE、estimate_duration
- `planning/backend/api-shape.md` §3, §5.5, §6.6 — Task 語義 + cancel_outcome 四種、Task DTO
- `planning/data/db-schema.md` §3.11 — tasks 表（T-003 已建）
- `planning/frontend/async-patterns.md` — SSE client 預期行為（對齊用）

---

## Acceptance criteria

- [ ] `docker compose up` 後 arq worker container 啟動且連上 Redis
- [ ] `POST /v1/tasks` 內部 helper（非 endpoint）建出 row + enqueue → worker 30s 內 pick up
- [ ] `GET /v1/tasks/{id}` 回結構正確的 Task DTO（含 `cancel_requested`、`queue_position` when queued）
- [ ] SSE：initial state 立刻推一筆；running 中 Redis publish 後 client 收到；terminal state 後 server 關 stream
- [ ] Cancel：queued → `cancelled_immediately`；running → `cancel_pending`；completed/failed 前已先到 → `too_late_*`；已 terminal → 409
- [ ] Cleanup cron 每小時跑一次，`COMPLETED_AT < NOW() - 24h` 的 row 被刪
- [ ] `pytest api/tests/tasks/` 全綠（含 SSE integration test 用 `httpx.AsyncClient`）

---

## Files expected to touch

- `api/app/workers/arq_worker.py` (new)
- `api/app/workers/jobs/noop.py` (new)
- `api/app/services/task_service.py` (new)
- `api/app/repositories/task_repo.py` (new)
- `api/app/routers/tasks.py` (new)
- `api/app/core/redis.py` (new) — Redis client + pubsub helper
- `api/app/core/settings.py` (edit) — 加 `REDIS_URL`
- `api/app/schemas/task.py` (new) — Pydantic Task DTO
- `api/app/main.py` (edit) — include router
- `api/tests/tasks/` (new) — 單元 + integration
- `infra/docker-compose.yml` (edit) — redis、worker service
- `planning/devops/environment-variables.md` (edit) — `REDIS_URL` 記錄

---

## Notes

- SSE 用 `StreamingResponse(media_type="text/event-stream")`；記得加 `X-Accel-Buffering: no` header（nginx 不會 buffer）
- `cancel_outcome` 判斷要在 **單一 SQL transaction** 內讀 + 寫，避免 race
- Cancel pending 後的最終狀態（cancelled / completed / failed）**不從 cancel endpoint 回傳**，client 靠 SSE / polling 追蹤
- Progress estimator loop（每 2s 推 elapsed / estimated）先在 T-014 實作，本單先定好 publish schema
- arq 的 `on_startup` / `on_shutdown` 綁 redis pool 與 DB session
- 單元測試用 `fakeredis` + in-memory SQLAlchemy 可（pubsub fakeredis 新版有支援）
