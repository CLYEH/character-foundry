# T-052: Authentik docker service 加入 stack

**Status:** TODO
**Sprint:** 3.5a
**Est:** S
**Depends on:** none
**Related:** T-053（Authentik client 註冊；本單只把 service 開起來，不配 IdP / client）

---

## Scope

把 Authentik OSS（server + worker + 專用 postgres + 專用 redis）加進 `docker-compose.yml`。Health check 通過、named volume 持續化。**不**做 upstream IdP 或 client 註冊（那是 T-053）。

**In scope:**
- 4 個 service：`authentik-postgres`、`authentik-redis`、`authentik-server`、`authentik-worker`
- Named volumes：`authentik_postgres_data`、`authentik_redis_data`、`authentik_media`
- `.env.example` 加 Authentik secrets（`AUTHENTIK_SECRET_KEY`、postgres password 等）
- `nginx/` 加 `/oauth/` 反向代理到 `authentik-server:9000`
- `docker compose up` 拉起來 health 全綠
- dev 與 prod compose 設定都加好（per devops D1：dev/prod parity）

**Not in scope:**
- Authentik admin UI 內的 IdP 與 client 設定（T-053）
- Backend / Frontend 接 OAuth（T-054 / T-056）
- Authentik 升級流程文件（M3.5 ship 前的 ticket）

---

## Planning refs

- `planning/devops/authentik-stack.md` §1 / §2 / §3 — service 組成、named volume 規則
- `planning/devops/environment-variables.md` — secrets 寫法慣例
- `STATUS.md` known risk S3-3 — bind-mount 與 worktree 衝突的歷史，本單刻意避開

---

## Acceptance criteria

- [ ] `docker compose up` 全綠（既有 services + 4 個新 authentik services）
- [ ] `curl http://localhost/oauth/-/health/ready/` 回 200
- [ ] `docker volume ls` 看到 3 個 `authentik_*` named volume
- [ ] `docker compose down && docker compose up` Authentik 設定持續（postgres 沒丟資料）
- [ ] `.env.example` 包含所有新 secret keys（不含實值）
- [ ] dev override 跑起來與 prod 同 service set（per D1 parity）

---

## Files expected to touch

- `docker-compose.yml` (edit) — 加 4 service + 3 volume
- `docker-compose.override.yml` (edit) — dev 用 port mapping / debug log
- `nginx/nginx.conf` (edit) — `/oauth/` proxy
- `.env.example` (edit) — Authentik secret keys
- `planning/devops/environment-variables.md` (edit) — 把新 env 補進列表
- `tickets/T-052-authentik-docker-service.md` (new — 本單)
- `STATUS.md` (edit) — 加 T-052 row 進 Sprint 3.5a

---

## OAuth scope required

`n/a`（infra ticket，沒新 endpoint）

---

## MCP tool delta

`n/a`（不動 MCP layer）

---

## Notes

- Authentik 自己用 `:9000` HTTP / `:9443` HTTPS。Phase 1 內網走 nginx 反代 `/oauth/`，不對外開 9000/9443
- `AUTHENTIK_SECRET_KEY` 用 `openssl rand -base64 32` 產，存進 1Password / secret manager，**不** commit 到 repo
- Authentik 預設 admin 帳號用 `akadmin` + 初次設定 token；setup 流程記錄到 `planning/devops/operations.md`（T-053 落地時補）
- 記憶體：Authentik server + worker + postgres + redis 大約多吃 1-1.5GB，dev 機器 docker desktop 預設 4GB 要調 8GB
