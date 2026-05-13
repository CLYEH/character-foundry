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

- Sprint 3.5a 開：「Authentik docker service 加入 stack」← T-052 ✅
- Sprint 3.5a 開：「Authentik 設定 Google upstream IdP + character-foundry 內部 client（claude-code / vs-code / cursor / cf-test-agent）」← T-053 (this doc §5)
- M3.5 ship 前：backup 流程寫進 `operations.md`

---

## 5. Initial setup（T-053 runbook）

> **Scope：** 一次性操作。把空白 Authentik 變成「Google login + 5 條 scope + 5 個 application」的 OAuth provider。下面步驟皆從 admin UI 操作。
>
> **Prerequisite：**
> - T-052 stack `docker compose up` 起來、`curl http://localhost/oauth/-/health/ready/` 回 200
> - 公司 Workspace admin 已在 Google Cloud Console 建好 OAuth 2.0 Client ID（web application 類型，redirect URI = `http://localhost/oauth/source/oauth/callback/google/` for dev，prod 換 host）
> - 拿到 `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` 填進 `.env`
> - `.env` 內 `AUTHENTIK_BOOTSTRAP_*` 暫時不設；首登走 recovery URL flow

### 5.1 Admin 首登

1. 啟動 stack 後抓 server container log 找 initial setup URL：
   ```bash
   docker compose logs authentik-server | grep -E 'recovery|setup|initial' | head -3
   ```
   或直接抓 worker log（worker 跑 startup 任務時會印 token）：
   ```bash
   docker compose logs authentik-worker | grep -i 'initial setup' | head -3
   ```
   訊息形式：`To finish setup, visit http://localhost/oauth/if/flow/initial-setup/?token=<token>`
2. 用瀏覽器打開該 URL（dev override 已把 `9000` / `9443` bind 到 127.0.0.1，若直接 `http://localhost:9000/...` 也可）
3. 設 admin 帳號（`akadmin`）密碼，存入 1Password / Bitwarden
4. ⚠ **不要**把 admin 密碼放進 `.env` — T-052 §Notes 已禁
5. 從此往後 admin login 走 `/oauth/if/flow/default-authentication-flow/` 或 `http://localhost:9000/if/admin/`

### 5.2 設 Google upstream IdP

目標：使用者按「Sign in with Google」→ Authentik → Google → 回 Authentik → 建立或匹配 user → issue session。

1. Admin → **Directory → Federation & Social login → Create** → Type: **OAuth Source**
2. 填：
   - **Name**: `Google Workspace`
   - **Slug**: `google`（影響 callback URL 路徑 = `/source/oauth/callback/google/`，要和 Google Cloud Console 設的 redirect URI 一致）
   - **Provider type**: `Google`（內建 preset 會自動填 authorization / token / profile endpoint）
   - **Consumer key**: `${GOOGLE_OAUTH_CLIENT_ID}` from `.env`
   - **Consumer secret**: `${GOOGLE_OAUTH_CLIENT_SECRET}` from `.env`
   - **Scopes**: `openid profile email`
   - **User matching mode**: `Link to a user with identical email address`（Workspace 同 email 自動匹配既有 user，避免分裂帳號）
   - **⚠ Workspace domain restriction**: `Additional scopes` 或在 Authentik enrollment flow 加 policy 限定 `hd=<your-workspace-domain>`（例：`hd=character-foundry.com`）。**這條看似多餘但必填**：「Link to a user with identical email address」相依「upstream IdP 已驗 email」這個假設；Workspace 內 tenant-restricted account 提供這保證，**但 consumer Google 不**。今天只有 Workspace 沒事；未來操作者多接一條 OAuth Source（例：personal Google / GitHub）就會撞 classic account-takeover-by-email-claim — 攻擊者用 victim 的 `victim@example.com` alias 註一個 consumer Google，登入後 link 到 victim 既有 Authentik user。先把 `hd=` 鎖在 Workspace tenant 上 anchor 這個 trust assumption；之後任何「let me also enable X login」PR 必須直面這條
3. **Save**
4. 驗證：登出 admin → login 頁面應出現 Google 圖示 → 按下去 → Google consent → 回 Authentik dashboard 用 Workspace 帳號登入成功（admin 仍是另一個 user，這是測 user-side flow）

### 5.3 定義 5 條 scope

OAuth scope 在 Authentik 是 **Scope Mapping** 物件。Authentik 預設已建好 `openid` / `profile` / `email` / `offline_access` 4 條，本步驟加 5 條自訂 scope，對應 `app/auth/mcp_clients.py` 的 `CANONICAL_SCOPES`。

對每條 scope（共 5 次）：

1. Admin → **Customisation → Property Mappings → Create** → Type: **Scope Mapping**
2. 填：
   - **Name**: `cf-scope-character-read`（內部識別用）
   - **Scope name**: `character:read`（**這個字串會出現在 access token 的 `scope` claim，要逐字對齊 `app/auth/mcp_clients.py` `CANONICAL_SCOPES`**）
   - **Description**: `Read characters, bases, aliases, motions, checkpoints`
   - **Expression**: 留空（純授權標籤，不寫額外 claim 進 token；token 結構由 provider scope mapping 控制）
3. **Save**

重複上述步驟，依序建立：

| Scope name | Authentik object name | Description |
|---|---|---|
| `character:read` | `cf-scope-character-read` | Read characters, bases, aliases, motions, checkpoints |
| `character:write` | `cf-scope-character-write` | Create / mutate characters, bases, aliases, motions |
| `task:read` | `cf-scope-task-read` | Read async task status |
| `task:cancel` | `cf-scope-task-cancel` | Cancel a running async task |
| `usage:read` | `cf-scope-usage-read` | Read team usage / quota stats |

⚠ Scope name 拼錯會直接 break T-054 middleware（`require_scope("character:write")` 會找不到 match）。**逐字對齊** `app/auth/mcp_clients.py` 的 `CANONICAL_SCOPES`；對齊測 = `pytest api/tests/arch/test_mcp_clients_allowlist.py` 維持綠。

### 5.4 註冊 5 個 Application + Provider

Authentik 模型：**Provider**（OAuth flow 行為 — grant type、token TTL、signing）+ **Application**（給 user 看的展示物 + 對外 client_id 邊界）。1 個 application 接 1 個 provider，per-application 顆粒。

對每個 application（共 5 個）：

1. **建 Provider**：Admin → **Applications → Providers → Create** → Type: **OAuth2/OpenID Provider**
   - **Name**: `character-foundry-spa` / `claude-code` / `vs-code` / `cursor` / `cf-test-agent`
   - **Authentication flow**: `default-authentication-flow`
   - **Authorization flow**: `default-provider-authorization-explicit-consent`（delegated agent 拿 token 要明確同意；M2M 走 client_credentials 不走這條 flow，但 `cf-test-agent` 仍要鎖死 grant types — 見下方 cf-test-agent 行備註）
   - **Client type / Client ID / Client Secret**: 見下表
   - **Redirect URIs / Origins**: 見下表
   - **Signing Key**: `authentik Self-signed Certificate`（Authentik 預設那把 RS256；T-054 backend 走 JWT verify 走它的 JWKS endpoint）
   - **Access token validity**: `hours=1`（per Q5 sub-5b — agent token 1h, no refresh; SPA human session 也用 1h 短，靠 refresh token 續）
   - **Refresh token validity**: 見下表
   - **Scopes**: 5 自訂 scope（character:read / character:write / task:read / task:cancel / usage:read）+ Authentik 預設的 `openid` / `profile` / `email`。**`offline_access` 只給 SPA**（其他 delegated agent 一律拿掉，見下表 Refresh token 欄）
2. **建 Application**：Admin → **Applications → Applications → Create**
   - **Name** / **Slug**: 同 provider name
   - **Provider**: 剛建好的那個
   - **Policy engine mode**: `any`

每個 application 的配置如下：

| App name | Client type | Client ID | Client Secret | Redirect URIs | Refresh token | 備註 |
|---|---|---|---|---|---|---|
| `character-foundry-spa` | **Public** (PKCE only) | `character-foundry-spa` | _(空 — public client)_ | `http://localhost:5173/auth/callback` (dev), `https://<prod-host>/auth/callback` | **30d**：provider scope list 含 `offline_access`；refresh token validity = `days=30` | T-056 frontend SPA 用，Auth Code + PKCE，無 secret |
| `claude-code` | **Public** (PKCE only) | `claude-code` | _(空)_ | `http://localhost/oauth/if/flow/default-provider-authorization-explicit-consent/` (Authentik 內 device flow) 或 agent 提供的 loopback | **不發 refresh**：provider scope list **必須不含 `offline_access`**（Authentik 無「禁止發 refresh」開關；唯一機制就是 `offline_access` 不在 scope 就不發 refresh）。⚠ 不要在這條 provider 上把 `Refresh token validity` 留預設然後依賴「token 過期就重 consent」——只要 `offline_access` 還在 scope list，Authentik 就會發出 refresh token，僅是 TTL 較短，這比「不發」嚴格更糟（token 仍可在 TTL 內 replay 且繞過 user 重 consent）| delegated client，per Q5 sub-5b |
| `vs-code` | **Public** (PKCE only) | `vs-code` | _(空)_ | VS Code OAuth helper loopback (`http://127.0.0.1:<port>/callback`) | 同上：`offline_access` 拿掉 | delegated client |
| `cursor` | **Public** (PKCE only) | `cursor` | _(空)_ | Cursor OAuth helper loopback | 同上：`offline_access` 拿掉 | delegated client |
| `cf-test-agent` | **Confidential** | `cf-test-agent` | _(Authentik 自動產，記到 1Password)_ | _(不需要 — client_credentials grant 不走 redirect)_ | _(不發 — M2M)_ | CI smoke client。**Grant types 鎖死只允許 `client_credentials`**：在 provider 設定關掉 `authorization_code` / `implicit` / `refresh_token`。理由：未來若有人把 client type 從 Confidential 翻 Public 或加 Auth Code grant 來「debug」，explicit-consent flow 會被 reachable 到一個本該無人值守的 M2M client，blast radius 直接擴成 full delegated；把 grant type 邊界做進結構而不是 behavioural |

⚠ **Client ID 字串要逐字對齊 `app/auth/mcp_clients.py` `ALLOWED_CLIENTS` key**。打錯一個字母 T-054 middleware 就會 reject token。

⚠ `cf-test-agent` secret **不要進 `.env.example`**，只存 1Password；本機 dev / CI smoke runtime 把它讀進 `CF_TEST_AGENT_CLIENT_SECRET` env var（T-057 ship gate ticket 才實際用到，本單不必設）。

### 5.5 設 per-client scope policy（Group + Policy 綁定）

Q5 sub-5a：narrow default + per-client 覆寫。Authentik 的做法是 **Group** 控制誰能拿到一個 application + scope 組合。

1. **建 5 個 Group**（admin → Directory → Groups → Create）：
   - `cf-agent-default`（4 個 delegated client + 未來 M2M 的 narrow default 兜底）
   - `cf-test-agent-full`（只給 `cf-test-agent` 用，拿全 5 scope）
   - （未來 M2M client 額外 group 在這層加，不動 code）

2. **設 Group 成員**：
   - 把 Workspace 內每個會用 agent 的 user 加進 `cf-agent-default`（delegated grant 要 user 也在 group 才能授權）
   - `cf-test-agent` 走 client_credentials 不綁 user，但 Authentik 仍需要 `cf-test-agent-full` group 才能限定 issue scope；把 `cf-test-agent` 的 internal service account user 加進去

3. **設 Application access policy** (per application)：
   - Admin → **Applications → <app-name> → Policy / Group / User Bindings**
   - 對 4 個 delegated client：bind `cf-agent-default`
   - 對 `cf-test-agent`：bind `cf-test-agent-full`

4. **設 Provider scope 限制**（如果要真正卡死 narrow default）：
   - delegated provider scope 列表只放 `character:read` / `character:write` / `task:read` / `task:cancel` / `usage:read` 全 5 條（user 在 consent 時實際勾哪幾條 = delegated token 最後拿到哪幾條）
   - `cf-test-agent` provider 同樣列 5 條（M2M 拿到全 5 = `app/auth/mcp_clients.py` 顯式 override 對齊）

> **這節是「policy 怎麼長」最容易迷路的一節。Authentik 文件對 M2M scope 怎麼跟 group 互動講得不直觀**。落地時若 verification step（§5.6）某條失敗、且確定不是其他層的 bug，回來檢查：(a) provider scope 列表是否漏列、(b) group 是否漏加 user、(c) application policy binding 是否漏綁。

### 5.6 Verification

#### 5.6.1 Acceptance criterion #1（Google login via Authentik admin UI）

```
Admin logout → login 頁有 "Sign in with Google" 按鈕 → 按下去 → Google consent → 自動 redirect 回 Authentik → user 已建立並登入
```

#### 5.6.2 Acceptance criterion #7（curl client_credentials）

`cf-test-agent` 拿 access token：

```bash
# 從 1Password 把 secret 灌進 env，**不要**直接 paste 進 shell（會留在 ~/.bash_history /
# PowerShell ConsoleHost_history.txt）。bash：先 `set +o history` 再貼；fish / zsh 同理。
# PowerShell：`$env:CF_TEST_AGENT_CLIENT_SECRET = (op read "op://Vault/cf-test-agent/secret")` 之類。
export CF_TEST_AGENT_CLIENT_SECRET=...  # ← 從 1Password 取

# Authentik token endpoint = /oauth/application/o/token/（slug-style; check application detail page for the exact URL)
curl -X POST http://localhost/oauth/application/o/token/ \
  -d 'grant_type=client_credentials' \
  -d 'client_id=cf-test-agent' \
  --data-urlencode "client_secret=${CF_TEST_AGENT_CLIENT_SECRET}" \
  -d 'scope=character:read character:write task:read task:cancel usage:read'

unset CF_TEST_AGENT_CLIENT_SECRET  # 用完即清
```

預期 200 回應，body 形如：

```json
{
  "access_token": "<RS256 JWT — 3 base64URL segments separated by '.'>",
  "token_type": "Bearer",
  "expires_in": 3600,
  "scope": "character:read character:write task:read task:cancel usage:read"
}
```

把 `access_token` payload base64-decode（middle segment）後檢查 `scope` claim 含 5 條、`aud` claim 為 `cf-test-agent`。

如果 `scope` 回少幾條 → 多半是 §5.5 provider scope 列表 / group 綁定漏了某條。如果整個 token request 401 → 多半 client_id 拼錯或 client_secret 沒從 1Password 對齊。

#### 5.6.3 確認 access token 是 JWT 不是 opaque

`access_token` 是 3 段 `.` 串接的 JWT base64URL → 對。Authentik 預設 OAuth2/OpenID Provider 就是 JWT 格式；若拿到 opaque random string，回頭看 provider type 是不是被改成 introspection-only。

### 5.7 Backup / disaster recovery

Phase 1 不寫 automated backup；§5 setup 走過一遍要 1 小時，DB 倒掉重設 = 1 小時 + 把 `.env` 內 secrets 拷回 1Password。

M3.5 ship 前 backup 流程進 `operations.md`，包含：
- `docker run --rm -v authentik_postgres_data:/source -v $(pwd):/backup postgres:16-alpine pg_dump ...`
- `authentik_certs` named volume 一併備份（OIDC signing key；倒掉 = 所有 token 立即失效）
- 還原步驟驗證

### 5.8 Setup checklist

- [ ] §5.1 admin 首登完成、akadmin 密碼進 1Password
- [ ] §5.2 Google OAuth Source 設好；登出 admin 後 login 頁有 Google 按鈕；用 Workspace 帳號登入成功
- [ ] §5.3 5 條 scope（`character:read` / `character:write` / `task:read` / `task:cancel` / `usage:read`）都建好，name 逐字對齊
- [ ] §5.4 5 個 application（1 SPA + 4 agent）+ 對應 provider 都建好；client_id 逐字對齊 `app/auth/mcp_clients.py`
- [ ] §5.5 group + policy binding：delegated 4 個綁 `cf-agent-default`、`cf-test-agent` 綁 `cf-test-agent-full`
- [ ] §5.6.1 Google login flow 通
- [ ] §5.6.2 `curl` client_credentials 拿到 access token、scope claim 含 5 條
- [ ] §5.6.3 access_token 是 JWT 格式（3 段 base64URL）
- [ ] `cf-test-agent` client_secret 進 1Password，未進 `.env.example`
