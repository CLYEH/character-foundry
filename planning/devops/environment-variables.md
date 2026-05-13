# Character Foundry — Environment Variables

> **Status:** Draft v0.1 · 2026-04-23
> **Owner:** DevOps Agent
> **Purpose:** 完整 env var 清單，跨 agent 整理

---

## 1. 命名與管理

- **命名慣例：** `SCREAMING_SNAKE_CASE`
- **Frontend 需要**：`VITE_` 前綴（會 inline 進 bundle，禁放 secret）
- **Backend / Worker 需要**：無前綴
- **檔案位置：** `/srv/character-foundry/.env`（chmod 600, owner = app user）
- **Git：** `.env` 絕不進 git；repo 只有 `.env.example`（sanitized version）

---

## 2. 完整清單

### 2.1 Backend / API / Worker

| 變數 | 必填 | 範例 | 說明 | 敏感 |
|---|---|---|---|---|
| `DATABASE_URL` | ✓ | `postgresql+asyncpg://cf_app:xxx@postgres:5432/character_foundry` | DB 連線字串（含密碼）| 🔒 |
| `REDIS_URL` | ✓ | `redis://:xxx@redis:6379/0` | Redis 連線 | 🔒 |
| `STORAGE_ROOT` | ✓ | `/storage` | 容器內檔案儲存根目錄（mount 到 host 的 /srv/character-foundry/storage）| |
| `JWT_SECRET` | ✓ | `openssl rand -hex 32` 產出 | JWT 簽章金鑰 | 🔒 |
| `JWT_ACCESS_TTL_SECONDS` |  | `900` (15min) | Access token 有效期 | |
| `JWT_REFRESH_TTL_SECONDS` |  | `2592000` (30 天) | Refresh token 有效期 | |
| `STORAGE_SIGNED_URL_SECRET` | ✓ | `openssl rand -hex 32` | Storage signed URL 金鑰 | 🔒 |
| `STORAGE_SIGNED_URL_TTL_SECONDS` |  | `3600` (1hr) | Signed URL 預設有效期 | |
| `CORS_ALLOW_ORIGINS` |  | `https://character-foundry.internal` | 多個用 comma 分隔 | |

### 2.2 AI 模型 APIs

| 變數 | 必填 | 說明 | 敏感 |
|---|---|---|---|
| `OPENAI_API_KEY` | (✓ in prod) | gpt-image-2 呼叫用；`AI_STUB_MODE=true` 時可省略 | 🔒 |
| `AI_STUB_MODE` |  | `true`（預設，dev / CI / E2E）→ 走 StubAIClient 不打 provider；prod 設 `false` | |
| `OPENAI_API_BASE` |  | 預設 `https://api.openai.com/v1`；測試 / 私有代理可覆寫 | |
| `GPT_IMAGE_2_MODEL` |  | 預設 `gpt-image-2` | |
| `GPT_IMAGE_2_TIMEOUT_MS` |  | 預設 `60000` | |
| `GPT_IMAGE_2_MAX_RETRIES` |  | 預設 `3`（指數退避，每次 call 算一次 breaker failure）| |
| `AI_CIRCUIT_FAILURE_THRESHOLD` |  | 預設 `5`（連續失敗達此值即 OPEN）| |
| `AI_CIRCUIT_FAILURE_WINDOW_SECONDS` |  | 預設 `60`（sliding window 長度）| |
| `AI_CIRCUIT_OPEN_DURATION_SECONDS` |  | 預設 `300`（OPEN 持續秒數，TTL 自動恢復）| |
| `VEO_API_KEY` | ✓ | Veo 3.1 呼叫用（Gemini API / Vertex AI key）| 🔒 |
| `VEO_API_URL` | ✓ | API endpoint（Gemini API 或 Vertex AI endpoint）| |
| `VEO_MODEL` |  | 預設 `veo-3.1` | |
| `VEO_TIMEOUT_MS` |  | 預設 `180000` (3min) | |
| `VEO_MAX_RETRIES` |  | 預設 `2` | |
| `RECONCILER_MODEL` |  | 預設 `gpt-5-mini`（共用 `OPENAI_API_KEY`） | |
| `RECONCILER_TIMEOUT_MS` |  | 預設 `30000` | |
| `RECONCILER_MAX_TOKENS` |  | 預設 `800` | |

### 2.3 PostgreSQL（docker-compose 環境）

| 變數 | 必填 | 說明 | 敏感 |
|---|---|---|---|
| `POSTGRES_DB` | ✓ | `character_foundry` | |
| `POSTGRES_USER` | ✓ | `cf_app` | |
| `POSTGRES_PASSWORD` | ✓ | DB 密碼 | 🔒 |

### 2.4 Redis

| 變數 | 必填 | 說明 | 敏感 |
|---|---|---|---|
| `REDIS_PASSWORD` | ✓ | Redis 密碼 | 🔒 |

### 2.5 arq Worker / Scheduler 調整

| 變數 | 說明 | 預設 |
|---|---|---|
| `WORKER_CONCURRENCY` | 單 worker 併發任務數 | `4` |
| `WORKER_MAX_JOBS_PER_WORKER` | 單 worker 跑多少 job 重啟 | `1000` |

### 2.6 應用行為（optional tuning）

| 變數 | 說明 | 預設 |
|---|---|---|
| `LOG_LEVEL` | Python logging level | `INFO` |
| `LOG_FORMAT` | `json` 或 `text` | `json` |
| `SOFT_DELETE_RETENTION_DAYS` | Soft delete 保留天數 | `30` |
| `TASK_TERMINAL_RETENTION_HOURS` | Task 終止狀態保留 | `24` |
| `EXPORT_ZIP_RETENTION_DAYS` | Export ZIP 保留 | `7` |
| `STUCK_TASK_TIMEOUT_MINUTES` | Task stuck 閾值 | `60` |
| `USAGE_SOFT_LIMIT_USD` | UI 顯示軟上限 | `100` |

### 2.7 Monitoring

| 變數 | 必填 | 說明 | 敏感 |
|---|---|---|---|
| `PROMETHEUS_METRICS_ENABLED` |  | 預設 `true` | |
| `PROMETHEUS_METRICS_PORT` |  | 預設 `9090`（僅內網）| |
| `SENTRY_DSN` |  | optional，Phase 2 可接 | 🔒 |

### 2.8 Authentik OAuth provider (T-052)

| 變數 | 必填 | 說明 | 敏感 |
|---|---|---|---|
| `AUTHENTIK_SECRET_KEY` | ✓ | Authentik server / worker 共用，`openssl rand -base64 32` 產 | 🔒 |
| `AUTHENTIK_POSTGRES_PASSWORD` | ✓ | Authentik 專用 postgres instance 密碼（與主 app `POSTGRES_PASSWORD` 分開）| 🔒 |

> Authentik 自身的 postgres / redis 是 docker-compose 內專屬 instance，hostname `authentik-postgres` / `authentik-redis`，user/db 固定 `authentik`。T-053 起再加 upstream Google IdP / client_secret 等 env。

### 2.9 Frontend（VITE_ 前綴，**會 inline 到 bundle**）

| 變數 | 必填 | 範例 | 說明 |
|---|---|---|---|
| `VITE_API_BASE_URL` | ✓ | `/api` | API base path |
| `VITE_STORAGE_BASE_URL` | ✓ | `/storage` | Storage signed URL 前綴 |
| `VITE_APP_VERSION` | ✓ | `0.1.0` | UI 顯示版本（build 時注入）|
| `VITE_SENTRY_DSN` |  | 若用 Sentry frontend | |

**再次強調：** `VITE_*` **永遠**不放 secret。API key、JWT secret 等絕不以 `VITE_` 開頭。

---

## 3. `.env.example` 模板

repo 根目錄放這份（進 git，供參考）：

```bash
# ── Required secrets（填入實際值）──────────────────────
POSTGRES_PASSWORD=change_me
REDIS_PASSWORD=change_me
JWT_SECRET=change_me_32_bytes_hex
STORAGE_SIGNED_URL_SECRET=change_me_32_bytes_hex
AUTHENTIK_SECRET_KEY=change_me_base64_32
AUTHENTIK_POSTGRES_PASSWORD=change_me

OPENAI_API_KEY=sk-...              # gpt-image-2 + gpt-5-mini reconciler 共用
AI_STUB_MODE=true                  # dev/CI 預設；prod 設 false
VEO_API_KEY=...
VEO_API_URL=https://generativelanguage.googleapis.com/v1beta

# ── Infra URLs（docker compose 預設可用）────────────────
DATABASE_URL=postgresql+asyncpg://cf_app:${POSTGRES_PASSWORD}@postgres:5432/character_foundry
REDIS_URL=redis://:${REDIS_PASSWORD}@redis:6379/0

# ── Storage ──────────────────────────────────────────
STORAGE_ROOT=/storage

# ── Tunable（可留預設）─────────────────────────────────
WORKER_CONCURRENCY=4
LOG_LEVEL=INFO
SOFT_DELETE_RETENTION_DAYS=30
TASK_TERMINAL_RETENTION_HOURS=24

# ── Frontend ─────────────────────────────────────────
VITE_API_BASE_URL=/api
VITE_STORAGE_BASE_URL=/storage
VITE_APP_VERSION=0.1.0
```

---

## 4. Secret 產生腳本

`scripts/generate-secrets.sh`：

```bash
#!/bin/bash
# 產生新 .env 的隨機 secret 部分（套用時人工 concat）
set -e

cat <<EOF
POSTGRES_PASSWORD=$(openssl rand -base64 32 | tr -d '=/+')
REDIS_PASSWORD=$(openssl rand -base64 32 | tr -d '=/+')
JWT_SECRET=$(openssl rand -hex 32)
STORAGE_SIGNED_URL_SECRET=$(openssl rand -hex 32)
EOF
```

---

## 5. 檢查清單（初次部署）

- [ ] `.env` 放對位置且 chmod 600
- [ ] 所有 `🔒` 變數都填了真實值
- [ ] `DATABASE_URL` 的密碼跟 `POSTGRES_PASSWORD` 一致
- [ ] `REDIS_URL` 的密碼跟 `REDIS_PASSWORD` 一致
- [ ] 三個外部 API key 都是**專用 key**（非共用）
- [ ] `VITE_*` 沒有任何 secret
- [ ] `.env` 已加進 `.gitignore`

---

## 6. 關聯文件

- `deployment.md` — 部署架構 + secret 管理
- `operations.md` — 運維相關
- `../backend/ai-integration.md` — 具體 API client 使用這些 key
