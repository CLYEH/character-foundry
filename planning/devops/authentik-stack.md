# Authentik Docker Stack（M3.5）

> **Owner:** DevOps Agent
> **Created:** 2026-05-07
> **Status:** Locked（M3.5 plan phase Step 4）
> **Upstream:** `planning/auth/open-questions.md` Q1（Authentik OSS + Google upstream IdP）

---

## 1. dev/prod parity

### 1.1 規則

Authentik 在 dev / prod 都跑。dev `docker-compose.yml` 含 Authentik service，dev IdP 直接接 Google Workspace（與 prod 同 upstream IdP）。

### 1.2 為什麼

- Dev/prod parity：不跑 dev 等於 OAuth flow 完全沒測直到 staging
- Phase 1 你一個人開發；ship 前不該才測 OAuth
- 反方案（dev 不跑 Authentik，OAuth 在 staging 才驗）對 Phase 1 高風險

### 1.3 Caveat

- Dev 跑 Authentik 也耗資源（authentik server + worker container），Mac/Windows docker desktop 預設 memory 不夠要調
- Dev 的 Authentik config（client_id / redirect_uri）與 prod 不同，要分檔管

---

## 2. 資料 persistence

### 2.1 規則

Authentik 的 postgres data 用 **named docker volume**，不 bind-mount。

```yaml
# docker-compose.yml
volumes:
  authentik_postgres_data:
  authentik_redis_data:
  authentik_media:

services:
  authentik-postgres:
    image: postgres:16-alpine
    volumes:
      - authentik_postgres_data:/var/lib/postgresql/data
  authentik-redis:
    image: redis:alpine
    volumes:
      - authentik_redis_data:/data
  authentik-server:
    image: ghcr.io/goauthentik/server:latest
    volumes:
      - authentik_media:/media
```

### 2.2 為什麼不 bind-mount

`STATUS.md` S3-3 已記錄陷阱：`./` bind-mount source 永遠指向主 repo（不論 cwd 在哪 worktree）。多 worktree 共用一份 docker stack 時，bind-mount 會跨 worktree 污染。

Named volume 隔離乾淨，docker 自己管路徑，不受 worktree 結構影響。

### 2.3 Caveat

- Backup：named volume 不能直接 `cp -r ./data/...`，要 `docker run --rm -v authentik_postgres_data:/source ...` 或 `pg_dump`
- Phase 1 Authentik state（client 設定、token 簽發歷史）丟了就要重設一遍 → backup 流程要寫進 `planning/devops/operations.md`（M3.5 ship 前）

---

## 3. Stack 組成（最小可動）

```
authentik-postgres  (postgres:16-alpine)
authentik-redis     (redis:alpine)
authentik-server    (ghcr.io/goauthentik/server)
authentik-worker    (ghcr.io/goauthentik/server, --worker)
```

加上既有的 `api` / `worker` / `frontend` / `nginx` / `redis`（既有 backend redis 與 authentik redis 分開，避免 namespace 衝突）。

### 3.1 Networking

Authentik server 暴露在 nginx `/oauth/` 路徑（或 subdomain `auth.character-foundry.local`，dev hosts 加 entry）。

### 3.2 Secrets

`AUTHENTIK_SECRET_KEY` / postgres password / Google OAuth client_secret 走既有 `.env` pattern，per `planning/devops/environment-variables.md`。

---

## 4. 後續 ticket

- Sprint 3.5a 開：「Authentik docker service 加入 stack」
- Sprint 3.5a 開：「Authentik 設定 Google upstream IdP + character-foundry 內部 client（claude-code / vs-code / cursor / cf-test-agent）」
- M3.5 ship 前：backup 流程寫進 `operations.md`
