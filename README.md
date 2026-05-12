# Character Foundry

AI 角色生成平台。專案定位與 agent 切換見 [CLAUDE.md](./CLAUDE.md)，核心決策見 [DECISIONS.md](./DECISIONS.md)，當前進度見 [STATUS.md](./STATUS.md)。

---

## Prerequisites

- **Docker Engine 24+** 與 **Docker Compose v2**
- 本機直接跑 native dev server 時另需：**Python 3.12+**、**Node 20 LTS**、**pnpm 9**
- 產 secrets 用：`openssl`（Git Bash for Windows 內建）

---

## First-time setup（fresh clone 或 reset 後）

### 1. 寫 `.env`

```bash
cp .env.example .env
```

接著手動填這些 secrets（所有 `change_me*` placeholder 都要換掉）：

| Var | 怎麼填 |
|---|---|
| `POSTGRES_PASSWORD` | dev 隨便填一個（例：`devpw_local`） |
| `REDIS_PASSWORD` | 同上 |
| `JWT_SECRET` | 建議 `openssl rand -hex 32`（64 字元 hex，符合 HS256 hash 大小；code 不強制長度但這是 best practice） |
| `STORAGE_SIGNED_URL_SECRET` | 同上規格再產一次。**不要跟 `JWT_SECRET` 重用**——這兩個是不同 trust boundary（一個簽人類 session、一個簽資源 URL），重用會把攻擊面合併 |

⚠ **`DATABASE_URL` 與 `REDIS_URL` 內嵌的密碼必須跟上面一致**：改了 `POSTGRES_PASSWORD` / `REDIS_PASSWORD` 但忘改 URL 內嵌的那段，container 會 auth-failed 起不來。這是最常見的 first-time 雷。

PowerShell 也能產 hex：

```powershell
-join ((1..32) | ForEach-Object { '{0:x2}' -f (Get-Random -Min 0 -Max 256) })
```

`OPENAI_API_KEY` / `VEO_API_KEY` 在 dev 階段**留空即可**——backend 走 `api/app/ai/stub.py`，不會打外部 API。

### 2. Build images

```bash
docker compose build --no-cache
```

⚠ **不要加 `-f docker-compose.yml`**——明確指 `-f` 會跳過 `docker-compose.override.yml` 的 auto-merge，bind mount / dev override 都不會生效。讓 compose 自己合。

### 3. Up

```bash
docker compose up -d
docker compose ps     # 等 postgres + redis 變 (healthy) 才繼續下一步
```

### 4. 跑 migration

⚠ Postgres 必須先是 `(healthy)` 狀態，`docker compose exec` 不會等 healthcheck。

```bash
docker compose exec api alembic upgrade head
```

### 5. 種 user

```bash
docker compose exec api python -m app.cli seed-e2e
```

會印出：
```
created: test+alice@example.com
created: test+bob@example.com
```

再跑一次會印 `skipped:`——這指令是 idempotent，預期行為。

| Email | Password |
|---|---|
| `test+alice@example.com` | `TestPassword123!` |
| `test+bob@example.com` | `TestPassword123!` |

或自訂帳號：

```bash
docker compose exec api python -m app.cli create-user \
  --email leo@example.com --password 'MyPassword123!' --name Leo
```

### 6. 驗證

```bash
curl http://localhost/api/health
# → {"status":"ok","db":"ok","redis":"ok","storage":"ok"}
```

打開 **http://localhost/**，用 step 5 的帳號登入。

---

## 日常操作

```bash
docker compose up -d                   # 起
docker compose down                    # 停（保留資料 volume）
docker compose down -v                 # 停 + 清所有資料
docker compose logs -f api             # 看 backend log
docker compose logs -f web             # 看 frontend log
docker compose logs -f worker          # 看 arq worker log
docker compose ps                      # 容器狀態
docker compose exec api bash           # 進 api 容器
docker compose exec postgres psql -U cf_app -d character_foundry  # 進 db
```

### Native dev server（FE 想要 vite hot-reload）

```bash
docker compose up -d postgres redis api worker  # 後端留在 docker
pnpm -C web install
pnpm -C web dev                                  # → http://localhost:5173
```

`web/vite.config.ts` 已經把 `/api/*` proxy 到 `localhost:8000`。

### 跑測試

```bash
# Frontend
pnpm -C web test          # vitest unit tests
pnpm -C web typecheck
pnpm -C web lint
pnpm -C web format:check  # ⚠ 推 PR 前一定要跑，CI 會擋

# Backend（容器內跑）
# ⚠ 需要 DB 的測試會 skip / 失敗，除非 .env 設了 TEST_DATABASE_URL
# （見 .env.example：那個 URL 指到的 DB 會被 pytest DROP 所有 table，不要用主 DB）
docker compose exec api pytest

# Backend 含 coverage gate（reproduce CI；T-060）
# `fail_under = 75` 設在 api/pyproject.toml [tool.coverage.report]，
# 只在 --cov=app 加上去時才會觸發
docker compose exec api pytest --cov=app --cov-report=term

# Mutation testing baseline（T-060；本機 reproduce nightly workflow）
# 範圍由 api/pyproject.toml [tool.mutmut] paths_to_mutate 控制；
# 第一次跑會建 mutants/ workspace，之後 incremental。.harness/ bind-mount
# 在 docker-compose.override.yml 設好，所以 --baseline 用容器內 /app/.harness 路徑
docker compose exec api bash -lc "mutmut run && mutmut export-cicd-stats"
docker compose exec api bash -lc "python scripts/check_mutation_drift.py \\
  --stats mutants/mutmut-cicd-stats.json \\
  --baseline .harness/mutation-baseline.json"

# E2E（Playwright）
pnpm -C web e2e
```

---

## Troubleshooting

### `password authentication failed for user "cf_app"`

Postgres 的 `POSTGRES_PASSWORD` env var **只在第一次 init 時生效**。改完 `.env` 後既有 `pg_data` volume 還鎖著舊密碼。

**選一**：

```bash
# A. 清 volume 重來（無資料時用這個）
docker compose down -v
docker compose up -d
docker compose exec api alembic upgrade head
docker compose exec api python -m app.cli seed-e2e

# B. 不想清資料，進 db 改現有 user 密碼
docker compose exec postgres psql -U cf_app -d character_foundry \
  -c "ALTER USER cf_app WITH PASSWORD 'new_password_here';"
```

⚠ B 之後 `.env` 的 `POSTGRES_PASSWORD` + `DATABASE_URL` 內嵌密碼也要同步改成 `new_password_here`，否則下次起 container 又掛同一個錯。

### `web` 容器一直 restart：`Cannot find package 'vitest'`

`vite.config.ts` 從 `vitest/config` import `defineConfig`。anonymous volume `/app/node_modules` 是上次 build 留下來的，新加的 devDependencies 被舊 volume 蓋掉。

```bash
docker compose build --no-cache web
docker compose up -d --force-recreate --renew-anon-volumes
```

### `alembic upgrade FAILED Cannot locate revision XYZ`

container 看不到本地新增的 migration 檔。常見原因：

1. **下指令時帶了 `-f docker-compose.yml`**——這會跳過 `docker-compose.override.yml` 的 auto-merge，`./api/alembic:/app/alembic` bind mount 不生效。Drop `-f`。
2. **image build cache 是舊的**——`docker compose build --no-cache api` 重 build。

### API 502 + `ModuleNotFoundError: No module named 'X'`

新加的 Python dep 還沒進 image（`pip install` 是 build-time layer，bind mount 不會幫你裝）。

```bash
docker compose build --no-cache api
docker compose up -d --force-recreate api worker
```

### nginx 502 但 web container 是 Up

```bash
docker compose logs --tail=50 api      # 看 backend 是不是 boot 失敗
docker compose logs --tail=50 web      # 看前端 dev server 是不是 boot 失敗
```

通常是 api 上面那兩個 trap 之一。

---

## Repo 結構

```
api/                FastAPI backend + arq worker（同一個 image）
  app/              路由、service、AI client、storage backend
  alembic/          DB migrations
  tests/            pytest
web/                Vite + React 19 frontend
  src/api/          API client + queries + mutations
  src/components/   shadcn primitives + composite components
  src/routes/       page-level routes
infra/nginx/        反向代理設定
planning/           各 agent 的規格書（source of truth）
  product/ ux/ frontend/ backend/ data/ devops/
tickets/            實作工單；DONE/ 下是已完成
.github/            CI workflows + PR template
```

---

## 進一步

| 想找什麼 | 去哪 |
|---|---|
| 當前進度 / 下一張 ticket | [STATUS.md](./STATUS.md) |
| 核心架構決策 | [DECISIONS.md](./DECISIONS.md) |
| Git / PR / Codex review 規則 | [CONTRIBUTING.md](./CONTRIBUTING.md) |
| Agent 角色切換 / 工作流程 | [CLAUDE.md](./CLAUDE.md) |
| API endpoint / 錯誤格式 | [planning/backend/api-shape.md](./planning/backend/api-shape.md) |
| 前端元件對應 | [planning/frontend/component-map.md](./planning/frontend/component-map.md) |
| Wireframes | [planning/ux/wireframes.md](./planning/ux/wireframes.md) |
| Env 變數定義 | [planning/devops/environment-variables.md](./planning/devops/environment-variables.md) |
| Nightly provider contract replay（test API key 註冊 / spending cap / triage） | [planning/devops/operations.md §7](./planning/devops/operations.md) |
