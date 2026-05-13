# T-067: Harden docker-compose secret interpolation + minimal container posture

**Status:** TODO
**Sprint:** Harness B-tier follow-ups
**Est:** S
**Depends on:** none
**Related:** T-052（OAuth migration 第一張，本單從 T-052 PR #85 Codex P1 + security review pushback 拆出）

---

## Scope

把 `docker-compose.yml` 所有 secret 類 env 從 bare `${VAR}` 改成 required-interpolation `${VAR:?must be set}`，並一輪 baseline harden（`read_only`、`cap_drop: [ALL]`）。一張單統一掃，不再每張 OAuth / migration 票都重論一遍。

**In scope:**
- 所有 secret 類 env interpolation 套 `:?must be set` guard（含既有 `POSTGRES_PASSWORD` / `REDIS_PASSWORD` / `JWT_SECRET` / `STORAGE_SIGNED_URL_SECRET` + T-052 加的 `AUTHENTIK_POSTGRES_PASSWORD` / `AUTHENTIK_SECRET_KEY`）
- `.env.example` 註解標明為什麼 guard 存在（防 silent empty interpolation）
- CI 的 `.env` heredoc 在 PR / e2e workflow 維持齊全（已是現狀，順手 sanity check）
- Baseline container harden：每個 service 加 `read_only: true`（write path 走 named volume tmpfs / volumes）+ `cap_drop: [ALL]` + 必要時 `cap_add` minimum set
- 跑一次 `docker compose config --quiet` + 整套 e2e 確認沒 regress

**Not in scope:**
- 改 image pin policy（digest pin 排 M3.5 ship-prep）
- 改 authentik-redis 加 `--requirepass`（網路隔離 sufficient，T-052 已寫明 intent）
- 改 postgres `pg_hba.conf` 加白名單（同上，網路 isolation）
- `/oauth/` WebSocket upgrade headers（T-053 admin UI 落地時再加）
- 改 `nginx depends_on` 加 `condition: service_healthy`（T-052 刻意避開）

---

## Planning refs

- `planning/devops/authentik-stack.md` §3.2 — secret 管理慣例
- `planning/devops/environment-variables.md` §2 — env var sensitive 標記
- PR #85 Codex P1 + security review thread — 觸發脈絡

---

## Acceptance criteria

- [ ] `docker-compose.yml` 所有 secret 類 env 都用 `${VAR:?must be set}`
- [ ] 故意拿掉一條 secret 跑 `docker compose config` 立即在 stderr 看到該 var 的 error message，exit 非零
- [ ] 每個 service 加 `read_only: true` + `cap_drop: [ALL]`（後續可 cap_add minimum set）
- [ ] CI e2e 全綠（含 T-049 e2e gate）
- [ ] `.env.example` 對應 secret 都有 placeholder（已是現狀；sanity check）

---

## Files expected to touch

- `docker-compose.yml` (edit) — secret interpolation + read_only + cap_drop
- `.env.example` (edit) — 註解 guard 行為（非必要 placeholder 改動）
- `planning/devops/environment-variables.md` (edit) — §1 加 guard pattern 說明

---

## OAuth scope required

`n/a`（infra ticket，沒新 endpoint）

---

## MCP tool delta

`n/a`（不動 MCP layer）

---

## Notes

- Codex P1 [PR #85 thread r3231654594](https://github.com/CLYEH/character-foundry/pull/85#discussion_r3231654594) 原始建議只針對 Authentik 兩條，被 defer 是因為「partial harden 反而留下不一致」。本單就是承諾的一次到位 refactor。
- Security review 同時提的 `read_only` / `cap_drop` 一起做，避免再開一張 ticket。
- 預期撞點：`read_only: true` 後 service 內部有 write path 要全部 surface 出來走 volume — `api` / `worker` / `web` 各別 audit；`postgres` / `redis` / `authentik-*` 自己 image 就 hardened，main loop write 已走 volume。
- 排程：M3.5 ship 後 backlog 第一張 harden batch，可在 Sprint 3.5a 完成後插。
