# T-001: Repo Scaffolding

**Status:** DONE
**Sprint:** 0
**Est:** M (2h)
**Depends on:** none
**Related:** T-002, T-004, T-007

---

## Scope

建立 monorepo 骨架，讓 `docker compose up` 能跑起整套最小 stack：Postgres + Redis + 空的 FastAPI + 空的 React。

**In scope:**
- Monorepo 目錄結構（`api/` `web/` `infra/`）
- `docker-compose.yml`（postgres / redis / api / web / nginx）
- `docker-compose.override.yml`（local dev 用，bind-mount source）
- API：FastAPI 最小專案，`/health` endpoint 回 `{"status":"ok"}`
- Web：Vite + React 19 + TypeScript，顯示一個「Character Foundry」佔位頁
- `nginx.conf`（reverse proxy + 靜態檔 serve）
- `.env.example`（完整列 Phase 1 所有變數，敏感欄位空值）
- `.gitignore`（含 `.env`、`__pycache__`、`node_modules`、`dist/` 等）
- Repo root `README.md`（簡短：怎麼啟動）

**Not in scope:**
- Migrations（T-002）
- Auth（T-006）
- CI（T-004）
- 任何業務邏輯

---

## Planning refs

- `planning/devops/deployment.md` §3 — Docker compose 結構
- `planning/devops/environment-variables.md` §3 — `.env.example` 清單
- `planning/frontend/architecture.md` §2 — web 專案結構
- `planning/backend/api-shape.md` §5.9 — `/health` schema

---

## Acceptance criteria

- [ ] `docker compose up -d` 四個服務全起（postgres / redis / api / nginx 外加 web dev 或已 build 的 static）
- [ ] `curl http://localhost/api/health` 回 `{"status":"ok"}`
- [ ] `http://localhost/` 顯示 Character Foundry 佔位頁（純 HTML OK）
- [ ] `docker compose down` 不留垃圾（volumes 保留）
- [ ] `.env.example` 拷貝成 `.env` 後無需改即可啟動 dev 環境
- [ ] `README.md` 有：prereq、啟動步驟、`/health` 驗證

---

## Files expected to touch

- `docker-compose.yml` (new)
- `docker-compose.override.yml` (new)
- `.env.example` (new)
- `.gitignore` (new)
- `README.md` (new)
- `api/pyproject.toml` (new)
- `api/Dockerfile` (new)
- `api/app/__init__.py` (new)
- `api/app/main.py` (new) — FastAPI 實例 + `/health`
- `web/package.json` (new)
- `web/vite.config.ts` (new)
- `web/tsconfig.json` (new)
- `web/index.html` (new)
- `web/src/main.tsx` (new)
- `web/src/App.tsx` (new) — 佔位
- `infra/nginx/nginx.conf` (new)

---

## Notes

- Python 3.12+、Node 20 LTS、pnpm 9
- PG image 用 `pgvector/pgvector:pg15`（避免之後換）
- 這張單故意不碰 Tailwind / shadcn（Frontend 完整 scaffolding 在 T-007）
- `/health` 只需回靜態 ok，**不檢查** DB / Redis 連線（T-009 才做真 health check）
- nginx 先做最簡單 reverse proxy，SSE / TLS 之後再加
