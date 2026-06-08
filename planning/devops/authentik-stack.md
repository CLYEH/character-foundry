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

> **2026-05-14: operator persona pass retrofit (T-069)** — §5.2 補 OAuth Source 的 Authentication / Enrollment flow 設定、新增 §5.7「Provision a dev operator」。原 §5.7 / §5.8 順移為 §5.8 / §5.9。觸發：T-068 SPA 三入口登入的 dev 測試 reveal「真人 operator 從零登入」三道牆連環卡（見 `tickets/DONE/T-069-*.md`）。
>
> **2026-05-18: operator persona pass retrofit (T-077)** — §5.7 新增 §5.7.3「Operator group membership」、§5.9 checklist 同步補一條。觸發：T-076 CDP 驗證 reveal wall 5 —— §5.5 「把 user 加進 `cf-agent-default`」是給 agent client 寫的，operator-provisioning runbook 從沒提，新 operator 過了 Authentik 登入卻被 `/oauth/application/o/authorize/` 用 "Permission denied" 擋掉。

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
   - **Slug**: `google`（影響 Authentik 內部 callback 路徑 = `/source/oauth/callback/google/`；nginx 反代 `/oauth/` → Authentik 後，**外部** callback URL 為 `https://<authentik-host>/oauth/source/oauth/callback/google/`，必須**逐字**等於 Google Cloud Console 那邊填的 Authorized redirect URI — 漏掉 `/oauth/` 前綴會在 IdP flow 第一跳就 `redirect_uri_mismatch`）
   - **Provider type**: `Google`（內建 preset 會自動填 authorization / token / profile endpoint）
   - **Consumer key**: `${GOOGLE_OAUTH_CLIENT_ID}` from `.env`
   - **Consumer secret**: `${GOOGLE_OAUTH_CLIENT_SECRET}` from `.env`
   - **Scopes**: `openid profile email`
   - **User matching mode**: `Link to a user with identical email address`（Workspace 同 email 自動匹配既有 user，避免分裂帳號）
   - **Authentication flow**: `default-source-authentication`（matched user 登入時走這條；**漏設的話連 `email_link` 匹配成功的既有 user 都登不進去**）。⚠ **2026-05-15 T-078**：這條 flow 的 `authentication` 欄要從 Authentik 出廠的 `require_unauthenticated` 改成 `none` —— SPA logout 只 revoke OAuth refresh token，Authentik 的 `authentik_session` cookie 會留下，下次 Google 登入走進這條 flow 時 `FlowPlanner.plan()` 會以 "Flow does not apply to current user" 拒絕已 authenticated 的 operator（T-078 wall 6）。Dev 端 admin UI 改完即可；CI / e2e 由 `infra/authentik/blueprints/cf-e2e-bootstrap.yaml` 的 `authentik_flows.flow` upsert 強制成 `none`，blueprint apply 即生效。本 codebase 只用一條 OAuth Source（Workspace Google），這條 flow 沒有「必須斷未 authenticated」的功能需求；未來加第二條 source 又有此需求時，clone 一條 source-specific 的 auth flow，不要把這條再鎖回 `require_unauthenticated`。
   - **Enrollment flow**: `default-source-enrollment`（沒 matched user 時走這條自動建新 Authentik user；**漏設就是 dev 測試踩到的 `authentik Logo / Bad Request / Source is not configured for enrollment`**）
   - **⚠ Workspace domain restriction**: `Additional scopes` 或在 Authentik enrollment flow 加 policy 限定 `hd=<your-workspace-domain>`（例：`hd=character-foundry.com`）。**這條看似多餘但必填**：「Link to a user with identical email address」相依「upstream IdP 已驗 email」這個假設；Workspace 內 tenant-restricted account 提供這保證，**但 consumer Google 不**。今天只有 Workspace 沒事；未來操作者多接一條 OAuth Source（例：personal Google / GitHub）就會撞 classic account-takeover-by-email-claim — 攻擊者用 victim 的 `victim@example.com` alias 註一個 consumer Google，登入後 link 到 victim 既有 Authentik user。先把 `hd=` 鎖在 Workspace tenant 上 anchor 這個 trust assumption；之後任何「let me also enable X login」PR 必須直面這條

> **這兩個 flow Authentik 安裝時就內建**（`designation` 各為 `authentication` / `enrollment`），下拉直接選即可，不必自建。T-053 原版 §5.2 只列了 name / slug / provider / key / scopes / matching mode，**漏了這兩欄** → 照舊版做出來的 Google source 一定壞（matched user 登不進、新 user enroll 不了）。這是 §5.2 spec 本身的缺口，不是哪張 ticket 的 regression（T-069 修）。

3. **Save**
4. 驗證：登出 admin → login 頁面應出現 Google 圖示 → 按下去 → Google consent → 回 Authentik dashboard 用 Workspace 帳號登入成功（admin 仍是另一個 user，這是測 user-side flow）

### 5.2.1 Source-init `next`-propagation — 走 `cf-google-init` flow，不要直連 source-init URL

> **2026-05-14: operator persona pass retrofit (T-073)** — 補 §5.2 的 wall 3：source enrollment / authentication flow 完成後不 redirect 回 SPA，operator 首登 dead-end 在 `/if/user/`。
> **2026-05-14: T-075** — 修 T-073 的 SPA URL 包錯一層（`?query=` vs plain `?next=`）；下面 SPA URL shape + 驗證步驟已是 T-075 修正後的版本。
> **2026-05-15: T-076** — 修 wall 4：dev `:5173` 下 flow interface 的 bootstrap XHR 跨來源被 CORS 擋。修法是 `VITE_AUTHENTIK_AUTHORIZE_URL` 改**絕對**（指 Authentik 真實 origin），詳見下方「§5.2.1a」。CDP 已驗證 fresh-session Google 登入 end-to-end 走到 Dashboard。
> **2026-05-18: T-074** — `next`-propagation 預設可被武器化成 open redirect（Authentik core `_flow_done` 信 `PLAN_CONTEXT_REDIRECT` 不驗 host）。修法是加一條 ExpressionPolicy 綁在 `cf-google-init` flow，plan time 檢查 `next` 是相對或同 host，否則拒。詳見下方「§5.2.1b」。

SPA 的「使用 Google 登入」**不能**直接導到 Authentik 的 `/oauth/source/oauth/login/<slug>/?next=<authorize URL>`。Authentik 2024.12.5 的 OAuth `OAuthRedirect` view（`authentik/sources/oauth/views/redirect.py`）**靜默忽略 `?next=`** —— 它從不把 `next` 寫進 session。`next` 只有在 `SESSION_KEY_GET` 被填好時才會被 honor，而那個 key 只由 **flow executor 的 dispatch**（`authentik/flows/views/executor.py:179`）寫入，bare 的 OAuth source-init path 不會。所以 Google callback 回到 `SourceFlowManager._prepare_flow` 時 `SESSION_KEY_GET[next]` 是空的，`final_redirect` fallback 到 `authentik_core:if-user`（`/if/user/`）—— operator 卡在 Authentik user 頁。（SAML source 沒這問題，因為 `authentik/sources/saml/views.py:160` 有顯式寫 `SESSION_KEY_GET`；OAuth source path 沒有對等實作。）

> ⚠ 這影響 **enrollment 與 authentication 兩條 flow** —— 兩者都走 `_prepare_flow`。原 T-073 ticket 假設「authentication flow 會 honor `next`、只有 enrollment 不會」是錯的：bare source-init 進來時兩條都不會。

**修法 —— `cf-google-init` launcher flow（blueprint codified）：**

SPA 改導到 `/oauth/if/flow/cf-google-init/?next=<authorize URL>` —— `next` 是**普通 query param、單層編碼**。這條 flow 只有一個 **RedirectStage**（static mode → `/oauth/source/oauth/login/google/`）：

```
SPA → /oauth/if/flow/cf-google-init/?next=<authorize URL>
    → flow interface 前端把 location.search 整條 bundle 進 executor API
      的 ?query=（FlowInterface*.js: flowsExecutorGet({query: location
      .search.substring(1)})）→ executor dispatch QueryDict 出 {next: ...}
      → 寫 SESSION_KEY_GET={next: ...}
    → RedirectStage → /oauth/source/oauth/login/google/
    → Google → callback → _prepare_flow 讀 SESSION_KEY_GET（中間沒有
      flow-executor dispatch，所以還在）→ 把 PLAN_CONTEXT_REDIRECT
      baked 進 enrollment / authentication plan
    → _flow_done() 優先 honor PLAN_CONTEXT_REDIRECT → 回 SPA authorize URL
```

> ⚠ **兩層 API 對 `next` 的傳遞慣例不同（T-073 踩過、T-075 修）：**
> - **Flow executor API**（`/api/v3/flows/executor/<slug>/`）讀 `?query=<urlencoded querystring>`。
> - **Flow interface**（`/if/flow/<slug>/`，SPA 實際打的）吃**普通 query params**（`?next=X`），由前端自己 bundle 進 executor 的 `?query=`。
>
> SPA 一定是打 interface。所以 SPA URL 用 plain `?next=`，**不要**自己先包成 `?query=next=` —— 那樣會 double-bundle 成 `{query: "next=X"}`，executor 拿不到 `next` key，`_prepare_flow` 還是 fallback 到 `/if/user/`（T-075 修的就是這個）。

flow / stage / binding 全部 codify 在 `infra/authentik/blueprints/cf-google-init.yaml`（純結構物件，無 secret / `!Env`）。Dev 由 `docker-compose.override.yml` 單檔 mount、e2e 由 `docker-compose.test.yml` 整個 dir mount 各自帶進去。**這就是「把 flow 設定 codify 一次解決 DB reset 重來」的落地**；OAuth Source 物件本身（§5.2）仍是 admin-UI / `ak shell` 管理（codify 它需要把 `GOOGLE_OAUTH_*` plumb 進 Authentik container env，T-073 刻意不做以保持 surgical）。

**SPA 端對應改動：** `web/src/lib/oauth-client.ts` 的 `buildSourceInitUrl` 改成產 `/oauth/if/flow/cf-google-init/?next=<authorize URL>`（非 bare source-init、也非 `?query=` 包裝）。`VITE_AUTHENTIK_GOOGLE_SOURCE_SLUG` 從此只當「按鈕顯不顯示」的 gate；實際 source 由 blueprint 固定。

**Verification：**

1. **Blueprint 有 apply**（Authentik 會吞掉 blueprint error —— 見 memory `authentik_blueprint_2024_12_gotchas`）：
   ```bash
   docker compose exec authentik-server ak shell -c \
     "from authentik.blueprints.models import BlueprintInstance as B; \
      b=B.objects.get(name='cf-google-init'); print(b.status, bool(b.last_applied_hash))"
   # 預期：successful True
   ```
2. **Flow executor API 回 redirect challenge**：
   ```bash
   # 注意：這是直接打 executor API（要 ?query=），不是 SPA 走的 interface 路徑。
   # `?query=` 的值是一條 urlencoded querystring；`next%3D%252Ffoo` 解一層成
   # `next=%2Ffoo`，executor 內 QueryDict 再解一層成 {next: '/foo'}。
   curl -s 'http://localhost/oauth/api/v3/flows/executor/cf-google-init/?query=next%3D%252Ffoo' \
     -H 'Accept: application/json'
   # 預期 body 含 "component": "xak-flow-redirect", "to": "/oauth/source/oauth/login/google/"
   # 同一個 request 的 session 寫入 authentik/flows/get（=SESSION_KEY_GET）= {'next': '/foo'}
   ```
   ⚠ 這只驗 executor API 那層；SPA 走 interface（`?next=`、前端自己 bundle），interface 那條只能靠 §3 的真實瀏覽器測。
3. **真人 operator 首登 end-to-end**（AC #4 —— 需真 Google 帳號 + 真瀏覽器，手動）：fresh browser（清掉 `authentik_session` cookie 模擬無 session）→ `:5173/login` → 「使用 Google 登入」→ Google → enrollment（選 username）→ **redirect 回 SPA → Dashboard**，不落在 `/if/user/`。沿用 CDP harness（memory `reference_local_chrome_cdp_connection`）。**這條是唯一能驗到 interface→executor 完整鏈的測試** —— OAuth/login 改動 ship 前務必先跑（memory `feedback_verify_oauth_flow_via_cdp_before_ship`）。

### 5.2.1a flow interface CORS — `VITE_AUTHENTIK_AUTHORIZE_URL` 要絕對（dev `:5173`）

> **2026-05-15: T-076** — 修 wall 4。

§5.2.1 修好 `next` 傳遞後，CDP 驗證 reveal 下一道牆：dev `:5173` 下 SPA 走 `/oauth/if/flow/cf-google-init/`（vite proxy → nginx → Authentik）載得起 flow interface HTML，但 interface 前端的 bootstrap XHR（`core/brands/current`、`root/config`、`flows/executor/...`）打的是 Authentik 的**絕對 `base_url`** `http://localhost/oauth/api/...`（port 80）—— 跟 SPA 所在 origin `http://localhost:5173` 跨來源 → CORS preflight 被擋 → interface 卡在 `Loading…`、RedirectStage 永遠沒機會跑。

**為什麼 `base_url` 是 `http://localhost`（無 `:5173`）：** `core/views/interface.py` 用 `request.build_absolute_uri()` 算 `base_url`，而 T-070 讓 nginx 用 `$host`（去 port）→ Authentik 看到 `Host: localhost`。`$host` 去 port 是 T-070 為了 Google `redirect_uri` 刻意設的，不能動。

**修法：`VITE_AUTHENTIK_AUTHORIZE_URL` 改絕對**（dev = `http://localhost/oauth/application/o/authorize/`）：

- SPA 的「使用 Google 登入」與「帳密」入口都是**導航**到 authorize URL（或從它 derive 的 `cf-google-init` flow URL）。authorize URL 絕對 → 導航直接到 Authentik 真實 origin `:80` → flow interface 從 `:80` 載 → 它的 XHR 打 `http://localhost/oauth/api/...` 同源 → 無 CORS。
- `redirect_uri` 仍是 `window.location.origin`（SPA 在 `:5173`）→ `http://localhost:5173/auth/callback` → 登完回到 SPA。
- `VITE_AUTHENTIK_TOKEN_URL` / `LOGOUT_URL` 維持**相對** —— 它們是 SPA 從 `:5173` 發的 `fetch`，相對路徑同源、走 vite `/oauth/` proxy 正確。只有「導航去的」URL 需要絕對。
- 零前端 code 改動（`buildAuthorizeUrl` / `buildSourceInitUrl` / 帳密 path 都已能吃絕對 URL）。詳見 `.env.example` 的 `VITE_AUTHENTIK_AUTHORIZE_URL` 註解。

**這是 dev-`:5173`-only。** Prod / CI e2e 整套同 origin（`nginx:80`），相對或絕對都同源、無 CORS —— CI `pr.yml` 自己寫的 `.env` 維持相對即可。

**既有 dev `.env` 要手動更新：** `.env` 是 gitignored，每個 operator 自己一份。本單只改 committed 的 `.env.example`；既有 dev 環境要把自己 `.env` 的 `VITE_AUTHENTIK_AUTHORIZE_URL` 改成絕對，然後 `docker compose up -d web`（不是 `restart` —— `restart` 不重讀 `env_file`）。

> **Verification：** CDP fresh-session 跑 `:5173/login` → 「使用 Google 登入」→ 全程在 `:80` 跑（CDP console 無 CORS error）→ Google → callback → `default-source-authentication` → authorize → token → **落在 SPA Dashboard（`:5173/`，heading 我的角色）**。T-076 已這樣驗過。

### 5.2.1b `next` 必須 same-origin — 防 open redirect

> **2026-05-18: T-074** — 補 §5.2.1 留下的 open-redirect 漏洞。

§5.2.1 把 `next` 端到端串起來：SPA → cf-google-init flow → `SESSION_KEY_GET` → callback `_prepare_flow` → `PLAN_CONTEXT_REDIRECT` → `_flow_done()`。`_flow_done()` 看到 `PLAN_CONTEXT_REDIRECT` 就 `redirect()` 過去，**完全不驗 host**（`authentik/flows/views/executor.py:380-383` 有顯式 comment：「The context `redirect` variable can only be set by an expression policy or authentik itself, so we don't check if its an absolute URL or a relative one」）。

→ `/oauth/if/flow/cf-google-init/?next=https://evil.com` 在使用者走完登入後，會被導到 `evil.com`。Evil.com 拿不到 code / token（OAuth `redirect_uri` 仍由 provider 那邊定），但仍是經典 open redirect（phishing landing、cookie 設定、瀏覽器層攻擊跳板等）。

> ⚠ 同一漏洞存在於 **每一條 flow-executor URL**（`default-source-authentication` / `default-source-enrollment` / `default-authentication-flow` 都一樣），不只 `cf-google-init`。是 Authentik core 既有行為、T-073 沒引入也沒加劇。

**修法 —— 在 `cf-google-init` flow 上綁一條 ExpressionPolicy（blueprint codified）：**

`infra/authentik/blueprints/cf-google-init.yaml` 新增（三個物件）：

1. **`authentik_policies_expression.expressionpolicy` `cf-google-init-next-validation`**：plan time 從 `http_request.session["authentik/flows/get"]` 讀 `next`，pure relative（無 scheme/netloc）OR 同 host（`urlparse(next).netloc.lower() == http_request.get_host().lower()`，case-insensitive per RFC 3986 §3.2.2）就放行，否則 `ak_message()` 拒。protocol-relative `//evil.com` / 反斜線變體 `/\evil.com` 在 urlparse 前 explicit-reject。**rejection 同時 `_deny()` 把 `SESSION_KEY_GET` 清空**（見「_flow_done fallback」）。
2. **`authentik_flows.flowstagebinding` 改 attrs**：`evaluate_on_plan: true` + `re_evaluate_policies: true`，讓 stage 政策同時在 plan time 跑 + stage 執行前 re-evaluate（覆蓋 race，見下）。
3. **兩條 `authentik_policies.policybinding`**：一條 target=flow（plan-time 主要路徑，rejection 觸發 `FlowNonApplicableException` → `handle_invalid_flow` 把 `ak_message` 顯示成 `ak-stage-access-denied`），一條 target=binding-redirect（race protection，rejection 移除 stage、session 已被 `_deny()` 清過所以 fallback 安全）。

**為什麼需要 race protection（stage-level 再 evaluate）：** Authentik executor `dispatch()`（`/authentik/flows/views/executor.py:179`）**無條件** `request.session[SESSION_KEY_GET] = get_params`，在 `if not self.plan` 之前。一旦 plan 已在 session 緩存（第一次 hit 用 legit `next` 成功 plan），第二次 hit 用 evil `next` 會**只覆蓋 SESSION_KEY_GET、不重 plan**（policy 不重跑）；RedirectStage 用緩存 plan 走完，`_prepare_flow` 後 `PLAN_CONTEXT_REDIRECT` 被 baked 成新的 evil 值。**ReevaluateMarker** 是 close 這條 race 的機制：stage 即將執行前重跑 policy，新 SESSION_KEY_GET 被讀到、拒。同一個 session 連跑 hit 1（legit）→ hit 2（evil），hit 2 必被擋。

**為什麼 `_deny()` 要清 SESSION_KEY_GET（不只是回 False）：** Policy 拒了之後 stage 被從 plan 拿掉、plan 空 → executor 直接呼 `_flow_done()`（executor.py:380-393）。`_flow_done` 有**自己一條 open-redirect surface**：若 `PLAN_CONTEXT_REDIRECT` 沒設，就讀 `SESSION_KEY_GET[next]` 然後 `redirect_with_qs(next)`，只要 `is_url_absolute()` 回 False。Authentik 的 `is_url_absolute` 只查 `bool(urlparse(url).netloc)`，所以：

- `/\evil.com`：netloc 空 → False → emit `Location: /\evil.com` → 瀏覽器（Chrome/Firefox/IE）把 `\` 當 `/` 解析 → 跨 origin 到 evil.com
- `javascript:alert(1)`：netloc 空 → False → `redirect_with_qs("javascript:alert(1)")` → Django `reverse("javascript:alert(1)")` 撞 `NoReverseMatch` → 500

兩條都是 policy 本意要防的 bypass。`_deny()` 清掉 `SESSION_KEY_GET["next"]` 後，`_flow_done` 的 fallback 改讀 `""`，落到 hardcoded 預設 `authentik_core:root-redirect`（reverse 到 `/if/user/`），安全。

**Trust invariants（policy 安全度依賴）：** `http_request.get_host()` 回的是 nginx 推上來的 `$host`（見 §2.1 的 `proxy_pass http://authentik_upstream;` + `proxy_set_header Host $host;`）。policy 同 host 檢查的有效性依賴：

- **`ALLOWED_HOSTS` 在 prod 必須**顯式 pin 到 deployment domain（不能 `*`）。Django `get_host()` 會驗 `ALLOWED_HOSTS`，未 pin → 任意 Host header 都通過。
- **nginx 必須 override（不是 append）進來的 `X-Forwarded-*` headers**。**今天已經做了** —— 見 `infra/nginx/nginx.conf` line 76-78 `proxy_set_header X-Forwarded-For $remote_addr; proxy_set_header X-Forwarded-Proto $scheme; proxy_set_header X-Forwarded-Host $host;`（`proxy_set_header` 是 replace 不是 append），跟註解 line 58-60 對齊。Authentik 預設 `USE_X_FORWARDED_HOST` 沒開、本 stack 沒設 `AUTHENTIK_LISTEN__TRUSTED_PROXY_CIDRS`，所以 `get_host()` 走 `Host` header（nginx 已用 `$host` 覆蓋）—— 安全。**未來若改 nginx 改成 append-style（`$proxy_add_x_forwarded_for` 風格）或打開 Authentik trusted proxy + `USE_X_FORWARDED_HOST`**，attacker controlled `X-Forwarded-Host` 就能繞過這條 policy。Prod nginx config 動到 X-Forwarded-* 區塊時 trip 這條 review。
- 這兩條任一被打破 → policy collapse 成形同虛設。Prod ship 前在 deploy checklist 驗證 `ALLOWED_HOSTS` 已 pin（X-Forwarded-* 那條已被 codified 在 nginx.conf）。

`FlowPlanner.plan()` 評 flow-level policy 在 RedirectStage 之前；policy 拒就整個 plan 中止，operator 看 `ak-stage-access-denied` 含 `ak_message` 文字。SPA 正常路徑（`next` 是同 host 的 authorize URL，T-076 後絕對形式也是 `http://localhost/oauth/application/o/authorize/...`）不受影響。

**Binding 範圍（含 T-079 擴張）：** 一條 policy 物件、五個 flow target。原始 T-074 binding 在 `cf-google-init`；T-079 把同一條 `cf-google-init-next-validation` 也綁到 **四條 Authentik 內建 flow**：

| Flow slug | 漏洞前 baseline（2026-05-26 curl 驗證） | T-079 binding | engine mode 動到 |
|---|---|---|---|
| `default-authentication-flow` | **完全無 `next` 驗證** —— 所有 evil 變體都進到 identification stage，victim 登入完 `_flow_done` 直接 redirect 走 | 兩條 binding：target=flow（plan-time）+ target=identification-stage-binding（race protection；既有 `re_evaluate_policies=True`）| 不動 —— flow 本來沒有任何 flow-level policy，單 policy 下 `any` ≡ `all` |
| `default-source-authentication` | 既有 `default-source-authentication-if-sso` 擋直接打（沒 SSO context 看 "Flow does not apply"），但**race 情境**（victim 已有 SSO context、attacker mid-flow overwrite SESSION_KEY_GET）仍可達 `_flow_done` | 兩條 binding：target=flow + target=login-stage-binding | **flip 到 `all`** —— 否則 if-sso True + 我們 False 在 `any` 下仍過 |
| `default-source-enrollment` | 同上 | 兩條 binding：target=flow + target=enrollment-login-stage-binding（最後 stage、無既有 policy） | **flip 到 `all`** —— 同上理由 |
| `default-source-pre-authentication` | Authentik 自帶 "Invalid next URL" 擋絕對 / protocol-relative，但**backslash 變體 `/\evil.com` slip 過** —— `is_url_absolute` 只看 `bool(urlparse.netloc)`，`\` 在 netloc empty → "relative" → `_flow_done` fallback `redirect_with_qs(/\evil.com)` → browser 解成 `//evil.com` | 一條 binding：target=flow（無 stages = 無 race window，flow-level 足夠）| 不動 |

`cf-google-init` 本身仍只有 target=flow + target=binding-redirect 兩條（T-074）。

**為什麼選哪條 stage 綁、為什麼不全部綁所有 stage：** T-074 dual-binding 的目的是 race protection，靠 `re_evaluate_policies=True` 的 ReevaluateMarker 在 stage transition 前再跑 policy。**只需要綁 flow 裡任意一個 stage binding** —— Marker 跑哪條取決於 stage 自身的 transition，attacker 走過任何一條 marker 都能擋住 race。我們刻意挑「無既有 PolicyBinding」的 stage（identification / login / enrollment-login），讓那條 stage binding 的 `policy_engine_mode` 保持 `any` 且只有單一 policy（mode-independent）—— 不必為了一條 race protection 多 flip 一個 stage 的 mode、累積未來新加 policy 的 blast radius。`default-source-enrollment-prompt` 是 enrollment flow 的 order=0，已有 `if-username` policy 在那；故意跳過、改綁 enrollment-login。

**為什麼 default-source-* 要 flip engine mode 到 `all`：** 兩條 flow 各有一條既有 flow-level `if-sso` policy。Authentik 預設 mode 是 `any`（任一 policy True → 整個 binding True）。race 情境 victim 已有 SSO context → if-sso True；attacker 把 `next` 覆寫成 evil → 我們 policy False。`any` 下整體 True、繞過。`all` 下 if-sso True + 我們 False = False、擋。Legit 情境（同 host next）兩條 policy 都 True、不變。`default-authentication-flow` / `default-source-pre-authentication` 沒有既有 flow-level policy，加我們是單一 policy，mode-independent，不必 flip（也不要 flip —— 不必要的 mode 動會增加 blast radius，下一個來加 policy 的人讀到 `all` 會以為是設計上要求兩條都過）。

**為什麼 stage 選擇是 last-stage（enrollment-login）而不是 first-stage（enrollment-prompt）：** 純為了避開既有 PolicyBinding，跟「越晚 race protection」無關 —— ReevaluateMarker fire 邏輯是 stage transition、不是「最後一道 stage」，所以哪條乾淨 stage 都同效。

**為什麼 policy 物件名仍叫 `cf-google-init-next-validation`：** 名字已 stale（綁到 5 條 flow 了），但 rename 要動 cf-google-init.yaml + 多個 `!Find` 引用、blast radius 比這條 readability 收益大。T-079 ship 後 doc 把 stale 名字事實註明（就在這節），rename 留給將來如果這條 policy 真的長到 third generation 再決定。

**為什麼不綁 `default-invalidation-flow` / `default-provider-authorization-*` 等其他 Authentik 內建 flow：** 攻擊面評估只列出可達 flow-executor URL + 會經由 `SESSION_KEY_GET` / `PLAN_CONTEXT_REDIRECT` 路徑做 redirect 的 flow。Invalidation flow 由 `UserLogoutStage` 控制 logout-then-redirect、不讀 `next`（T-078 ship 時驗過）；authorization flow 是 OAuth provider 自己的 `redirect_uri` provider-config-time 鎖死、不吃 attacker-controlled URL。如果未來加 multi-IdP 或新型 flow 是 attacker-reachable 且讀 `next`，再 retrofit。

**T-079 落地檔位：** `infra/authentik/blueprints/cf-builtin-flow-hardening.yaml`（policy 物件**獨立定義一份**、跟 `cf-google-init.yaml` 用同個 `identifiers: name` 走 idempotent upsert，**不**靠 cross-file `!Find`——避開 cold-boot blueprint discovery 順序 race）。Dev / e2e mount 同 cf-google-init.yaml 模式：dev 走 `docker-compose.override.yml` 單檔 mount，e2e 走 `docker-compose.test.yml` 整個 dir mount。Prod 仍跟 cf-google-init.yaml 一樣是 admin-UI 缺口（見 §5.9 checklist）—— 兩份 blueprint 同個 production codify 動作會在 M3.5 ship-prep 一次處理。

**Multi-stage race semantics（T-079 verification reveal）：** T-074 在單一 stage 的 cf-google-init 上，race deny 會把唯一一條 stage 移掉、整個 plan 變空、`_flow_done` fallback 回 `/oauth/` —— 看到的是乾淨的 "to: /oauth/" 回應。Multi-stage 的內建 flow 不一樣：race deny 把 marker 綁的那一條 stage 從 plan 拿掉之後 plan 還有其他 stage，executor 直接前進到下一條（例如 `default-authentication-flow` 把 identification 移掉後 victim 看到 password stage）。但是：
- `_deny()` 已經把 SESSION_KEY_GET 清空，所以即便 attacker 莫名其妙把 flow 推到結尾，`_flow_done` 也只能讀到空字串、走 hardcoded `authentik_core:root-redirect`、redirect 到 `/oauth/`。
- Attacker 看到的 password stage 沒有 `pending_user`（那是 identification 該設的），submit 任何密碼會回 "Unknown error"、flow 自動重啟回 identification。
- **整條 race 回應序列 grep 不到 `evil.com`**（2026-05-26 empirically verified）—— open-redirect invariant 保住。

兩種 race 結果（cf-google-init 乾淨 fallback、multi-stage broken-form dead-end）攻擊面結論相同：attacker 無法 redirect 到 evil。差別只在 dead-end 的 UX 樣態。Legit user 完全不踩 `_deny()` path、不受影響。

**Verification：**

```bash
# 1) 正當 SPA 用法：相對 next → xak-flow-redirect（pass）
curl -s 'http://localhost/oauth/api/v3/flows/executor/cf-google-init/?query=next%3D%252Foauth%252Fapplication%252Fo%252Fauthorize%252F' \
  -H 'Accept: application/json' | jq .component
# 預期: "xak-flow-redirect"

# 2) 同 host 絕對 URL（post-T-076 SPA 用法）→ xak-flow-redirect（pass）
curl -s 'http://localhost/oauth/api/v3/flows/executor/cf-google-init/?query=next%3Dhttp%253A%252F%252Flocalhost%252Foauth%252Fapplication%252Fo%252Fauthorize%252F' \
  -H 'Accept: application/json' | jq .component
# 預期: "xak-flow-redirect"

# 3) 攻擊：跨 origin → ak-stage-access-denied（block）
curl -s 'http://localhost/oauth/api/v3/flows/executor/cf-google-init/?query=next%3Dhttps%253A%252F%252Fevil.com' \
  -H 'Accept: application/json' | jq '{component, error_message}'
# 預期: component=ak-stage-access-denied, error_message="Refusing next URL outside same origin: https://evil.com"

# 4) 攻擊：protocol-relative → block
curl -s 'http://localhost/oauth/api/v3/flows/executor/cf-google-init/?query=next%3D%252F%252Fevil.com' \
  -H 'Accept: application/json' | jq .component
# 預期: "ak-stage-access-denied"

# 5) 攻擊：反斜線變體 → block
curl -s 'http://localhost/oauth/api/v3/flows/executor/cf-google-init/?query=next%3D%252F%255Cevil.com' \
  -H 'Accept: application/json' | jq .component
# 預期: "ak-stage-access-denied"
# （無防護則 _flow_done fallback 會 emit Location: /\evil.com，瀏覽器當 //evil.com 解）

# 6) 攻擊：userinfo trick → block
curl -s 'http://localhost/oauth/api/v3/flows/executor/cf-google-init/?query=next%3Dhttp%253A%252F%252Flocalhost%2540evil.com%252Fpath' \
  -H 'Accept: application/json' | jq .component
# 預期: "ak-stage-access-denied"
# （urlparse netloc='localhost@evil.com' != host 'localhost'，正確拒）

# 7) 攻擊：javascript: scheme → block（且不會 500）
curl -s 'http://localhost/oauth/api/v3/flows/executor/cf-google-init/?query=next%3Djavascript%253Aalert%25281%2529' \
  -H 'Accept: application/json' | jq .component
# 預期: "ak-stage-access-denied"
# （無防護則 _flow_done fallback redirect_with_qs 撞 NoReverseMatch 500）

# 8) RACE：同一 session 連跑 legit → evil，evil 必被擋
JAR=$(mktemp)
curl -sc $JAR -b $JAR 'http://localhost/oauth/api/v3/flows/executor/cf-google-init/?query=next%3D%252Foauth%252Fapplication%252Fo%252Fauthorize%252F' -H 'Accept: application/json' > /dev/null
curl -sc $JAR -b $JAR 'http://localhost/oauth/api/v3/flows/executor/cf-google-init/?query=next%3Dhttps%253A%252F%252Fevil.com' -H 'Accept: application/json' | jq '{component, to}'
rm -f $JAR
# 預期: component="xak-flow-redirect", to="/oauth/" (session sanitised, 安全 fallback)
# 注意：race path 故意 NOT 走 ak-stage-access-denied —— stage-binding rejection
# 移除 stage 後走 _flow_done fallback，session 已被 _deny() 清過所以 fallback
# 安全。完整解釋見 cf-google-init.yaml 的 dual-binding 註解。
```

Policy 物件直接看：

```bash
docker compose exec authentik-server ak shell -c \
  "from authentik.policies.expression.models import ExpressionPolicy; \
   from authentik.policies.models import PolicyBinding; \
   from authentik.flows.models import Flow; \
   f=Flow.objects.get(slug='cf-google-init'); \
   bs=PolicyBinding.objects.filter(target=f); \
   print('bindings:', bs.count(), [b.policy.name for b in bs])"
# 預期: bindings: 1 ['cf-google-init-next-validation']
```

### 5.3 定義 5 條 scope

OAuth scope 在 Authentik 是 **Scope Mapping** 物件。Authentik 預設已建好 `openid` / `profile` / `email` / `offline_access` 4 條，本步驟加 5 條自訂 scope，對應 `app/auth/mcp_clients.py` 的 `CANONICAL_SCOPES`。

對每條 scope（共 5 次）：

1. Admin → **Customisation → Property Mappings → Create** → Type: **Scope Mapping**
2. 填：
   - **Name**: `cf-scope-character-read`（內部識別用）
   - **Scope name**: `character:read`（**這個字串會出現在 access token 的 `scope` claim，要逐字對齊 `app/auth/mcp_clients.py` `CANONICAL_SCOPES`**）
   - **Description**: `Read characters, bases, aliases, motions, checkpoints`
   - **Expression**: `return {"scope": " ".join(token.scope)}`（**不可留空**——見下方 ⚠）
3. **Save**

> ⚠ **Expression 不可留空（S3.5-6 / T-093）。** Authentik 的 access-token JWT 只帶 OIDC
> claims + 各 *granted* scope 的 ScopeMapping expression 回傳的 dict
> （`id_token.py::get_claims` 把它們 merge 進 payload）。expression 留 `return {}` 的話，
> JWT 完全沒有頂層 `scope` claim，backend 的 `payload.get("scope")`（`app/auth/oauth.py`）
> 永遠是空字串，`/mcp/*` 的 per-scope 檢查全 fail——這正是 S3.5-6 的 root cause，T-084 /
> T-091 / 早期 grandfather 都在繞它。`return {"scope": " ".join(token.scope)}` 把 granted
> scope 集合以空白分隔字串寫進 `scope` claim（backend 要的就是 space-separated string）。
> 5 條 scope mapping 都填同一條（merge 冪等），任何 granted 子集都會帶出 claim。
> **驗證（operator）**：登入取一個 access token，decode JWT 中段 payload，確認 `scope`
> claim 含這 5 條（M2M client_credentials token 同理）。e2e 由 `cf-e2e-bootstrap.yaml`
> blueprint 鎖住、`api/tests/infra/test_authentik_scope_emission.py` 在 CI 守 expression 不被改回空。

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
| `character-foundry-mcp` | **Public** (PKCE only) | `character-foundry-mcp` | _(空 — public client)_ | **loopback regex** `^http://(127\.0\.0\.1\|localhost):[0-9]{1,5}(/.*)?$`（涵蓋 MCP Inspector `:6274` + native CLI ephemeral port）+ claude.ai connector `https://claude.ai/api/mcp/auth_callback`（strict） | **不發 refresh**：scope list **不含 `offline_access`**（同 delegated 規則）| **T-089**：真人 delegated MCP client（Claude Desktop / claude.ai / MCP Inspector / Cursor）**全部共用此一個 app**；MCP server 的 PRM（`/.well-known/oauth-protected-resource`）只宣告它當唯一 authorization server，故所有 MCP client 填 `client_id=character-foundry-mcp`。Authorization flow 用 explicit-consent（delegated 要真人同意）。redirect_uri 的精確值在 manual E2E 時確認 |

> **T-089 — `character-foundry-mcp` 是 §5.4 第 6 個 app。** 真人 MCP client 的 auto-login（discovery）走它；設計全貌見 `../agent-interface/mcp-oauth-discovery.md`。e2e/CI 由 `infra/authentik/blueprints/cf-e2e-bootstrap.yaml` codify（含所有 silent-failure gotcha：`invalidation_flow` 必填、`redirect_uris` 用 `{matching_mode,url}` list、`per_provider` issuer、Self-signed signing key）；**dev / prod 走本節 admin-UI 步驟**（與 SPA / agent app 同 pattern）。建好後把它的 issuer 加進 `AUTHENTIK_ISSUER_URL`、client_id 加進 `AUTHENTIK_AUDIENCE`（兩者皆 CSV），否則 `/mcp/` 對它的 delegated token 回 `AUTH_CLIENT_NOT_ALLOWED`。⚠ ngrok 手測時 token 的 `iss` 由 Authentik 依 request Host（ngrok host）算出，要把 `https://<ngrok-host>/oauth/application/o/character-foundry-mcp/` 一併加進 `AUTHENTIK_ISSUER_URL`。

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

### 5.7 Provision a dev operator

> **Scope：** 讓一個**真人 operator**（不是 e2e test user）能在 dev stack 從零走完 SPA 登入。§5.1–5.6 把 Authentik 變成 OAuth provider，但「operator 自己」這個帳號在 **Authentik** 和 **backend** 兩層都還不存在 —— dev stack 至今只被 e2e test user（`seed-e2e` 種的 `test+alice@ / test+bob@ / test+sprint2@`）涵蓋過。一個真人 operator 要能登入，**兩層 user 都要備妥**。

#### 5.7.1 Authentik user（enrollment flow 自動建，不需手動指令）

§5.2 的 OAuth Source 設好 **Enrollment flow** 後，operator **首次**按 SPA 的「使用 Google 登入」→ Google consent → 回 Authentik 時，因為 `email_link` matching 還匹配不到任何既有 user，Authentik 走 enrollment flow **自動建一個新 Authentik user**。

預期行為：第一次登入會跳一個 enrollment 中介頁要 operator **選 username**（email / name 從 Google profile 自動帶入，username 要自己填一個）。填完即建好；之後同一個 Google 帳號再登入就靠 `email_link` 直接匹配，不再跳 enrollment。

→ 這層**不需要手動指令**，flow 設好就自動。漏設 Enrollment flow 的後果見 §5.2（`Source is not configured for enrollment`）。

#### 5.7.2 Backend `User` row — 兩條 path

從 T-071 起 backend 有**兩條** path 建出 `users` row：自動 first-login（**主路徑**）和 `provision-operator` CLI（**break-glass / pre-provision**）。

##### 5.7.2.a 自動 first-login auto-provisioning（T-071，預設行為）

`api/app/api/deps.py::_resolve_oauth` 拿到 Authentik 驗過的 delegated token、但 `users` 表沒有 `claims.email` 對應 row 時，**自動建一個**（email + name from OIDC claim、default team）並放行。沒有手動 CLI 步驟。

**Guardrail（必設）：** `OAUTH_AUTO_PROVISION_ALLOWED_DOMAINS`（comma-separated email domain 清單）。Token email 的 domain 不在清單內 → 維持 401（`AUTH_INVALID_TOKEN`），不建 row。env var 沒設或留空 → fail-closed，**所有** first-login 都 401（=T-071 之前的行為）。

> **為什麼要有 backend 這層 domain 閘門：** §5.2 的 Google OAuth Source `hd=<workspace-domain>` 已經是上游 gate，理論上 Authentik 不會放跨 tenant 的 Google 帳號進來。但 backend 不應該假設上游一定鎖好——defense in depth：(a) 未來加第二條 OAuth Source 忘記設 `hd=` → backend 仍守住；(b) Authentik admin 不小心改掉 `hd=` policy → backend 仍守住。allowlist 設成跟 Workspace tenant 同樣的 domain 即可（例：`OAUTH_AUTO_PROVISION_ALLOWED_DOMAINS=character-foundry.com`）。
>
> **不做 per-email allowlist：** domain-only 是刻意選擇——細粒度的 per-email 控制屬於 `provision-operator` CLI（或未來 admin UI）的範圍。env var 做成 email allowlist 等於把 user 名單塞進 ops config，不是好的責任分離。

**Display name fallback：** OIDC `name` claim 有就用，沒有就退到 email local part（`alice@x.com` → `alice`）。超過 100 char 截斷以對齊 `users.name` VARCHAR(100)。Operator 想換 display name 走未來 admin UI（or `provision-operator` 預先建好）。

**Race 處理：** 同一 email 兩個 first-login request 同時進來時，`users.email` unique constraint 會擋第二個 INSERT —— `auto_provision_oauth_user` catch `IntegrityError` 後 re-select 出 winning row。Caller 看到兩個 200，row 只有一條。

##### 5.7.2.b `provision-operator` CLI（break-glass / pre-provision）

T-071 後，CLI 從「唯一補 row 方法」退回**特定情境**用：

- **Pre-provision**：在 operator 第一次打 API 之前先把 row 建好（例：CI 安排好測試用 operator，不想等首登 race）
- **Domain 不在 allowlist 但個案要放行**：operator 用個人 email 而非 Workspace（不該常見，但 break-glass 路徑保留）
- **Operator 一定要有特定 display name**：CLI 可指定 `--name`；自動 path 跟著 OIDC claim 走
- **Debug**：手動建 row 隔離問題，看 `_resolve_oauth` 後續是 token / scope / domain 哪一段壞

```bash
docker compose exec api python -m app.cli provision-operator \
  --email <operator-email> --name <operator-name> --team default
```

- `--email` 要**逐字等於** operator Google 帳號的 email —— `_resolve_oauth` 靠它匹配，差一個字就 401。
- `--name` 是 backend 顯示用名稱，可任意。
- `--team` 預設 `default`（Phase 1 單 team，見 DECISIONS §6 B5），一般不必帶。

> **為什麼是 `provision-operator` 而不是 `create-user`：** `create-user` 是給 JWT-login 帳密路徑用的，`--password` 必填。Operator 走 OAuth（Google，或 T-068 的 Authentik 帳密 fallback），backend `User` row 的 `password_hash` 對他的登入路徑沒有意義。`provision-operator` 建的 row 帶一個**隨機、不印出也不記錄**的 password hash —— backend JWT-login 路徑對這個 operator 等同停用，他只能走 OAuth。真的要給 operator 一條獨立帳密 break-glass 時才改用 `create-user`。

> **⚠ password fallback 也要這層 backend row。** 別誤以為改走 T-068 的「帳密 fallback」入口就能繞過 backend `User` row —— 帳密 path 走的是 Authentik 的 identification+password flow，SPA 拿到的一樣是 **Authentik OAuth token**，一樣會到 `_resolve_oauth` 的 email lookup。Backend `User` row 對**所有**登入入口都是必要的，跟走哪個入口無關。CLI 預建 / 自動 first-login 兩條 path 都行。

#### 5.7.3 Operator group membership — `cf-agent-default`（T-077，必做）

§5.7.1 + §5.7.2 把 Authentik user 和 backend `User` row 都備妥後，operator 仍會在 SPA 的最後一跳（`/oauth/application/o/authorize/`）被擋下，畫面是 Authentik 原生「Permission denied — Request has been denied」。原因：**`Character Foundry SPA` application 有一條 PolicyBinding 綁 `cf-agent-default` group**（§5.5 / e2e blueprint `cf-e2e-bootstrap.yaml` 都看得到），enrollment flow 建出來的新 Authentik user 預設不在任何 group → policy 拒。

§5.5 雖然寫了「把 Workspace 內每個會用 agent 的 user 加進 `cf-agent-default`」，但 §5.5 通篇是在講 **agent client** 的 group/policy 設定，操作者讀 §5.7 「provision a dev operator」時不會回頭去 §5.5 翻 group membership 這一步—— 這就是 T-077 wall 5 反覆踩到的原因。

##### 5.7.3.a 主路徑：first-login 後手動加 group（搭配 T-071 auto-provisioning）

T-071 後的預設 operator onboarding：

```
operator 開 SPA → 「使用 Google 登入」→ Google consent
  → Authentik enrollment flow（operator 選 username，§5.7.1）
  → callback → backend _resolve_oauth 自動建 User row（§5.7.2.a）
  → SPA 試走 /oauth/application/o/authorize/
  → ⛔ Permission denied（新 Authentik user 不在 cf-agent-default）
```

⚠ 這道牆 **T-071 解不了** —— T-071 補的是 backend `User` row，PolicyBinding 卡的是 Authentik-side group membership，兩層獨立。Operator 第一次撞牆後，**admin 必須手動補這步**，operator 重新登入才能進 SPA。

修法（admin 操作，從 stack host 跑一次即可）：

```bash
# 把 <operator-email> 換成 operator 在 Authentik 的 email（= Google email，
# 因為 enrollment flow 從 Google profile 帶入；同時也是 §5.7.2 backend
# User row 的 email，three-way 對齊）。
docker compose exec authentik-server ak shell -c \
  "from authentik.core.models import User, Group; \
   u = User.objects.get(email='<operator-email>'); \
   g = Group.objects.get(name='cf-agent-default'); \
   g.users.add(u); \
   print(f'added {u.username} to cf-agent-default')"
```

預期 stdout：`added <username> to cf-agent-default`。Operator 在 SPA 重整 / 重按「使用 Google 登入」就能通過 authorize 走到 Dashboard。

> **為什麼不用 admin UI：** admin UI 也可以（Directory → Groups → `cf-agent-default` → Users tab → Add），但 `ak shell` snippet 是可貼上的 single-line、不依賴點哪個 tab、可寫進 onboarding runbook，且跟 `cf-e2e-bootstrap.yaml` 用的 `users` FK 是同一個 attribute（見 memory `authentik_blueprint_2024_12_gotchas`：`users_obj` 是 read-only SerializerMethodField）。

##### 5.7.3.b 旁路：pre-provision 時順手加 group

若選擇 §5.7.2.b 的「pre-provision」path（admin 在 operator 第一次打 API 前就先建好 backend row，例：CI / 排好的 operator onboarding session），同一時間也可在 Authentik 端 pre-create user + 加 group，免去 §5.7.3.a 第一次撞牆後再回來補的循環：

```bash
# 1. 在 Authentik 端 pre-create user
docker compose exec authentik-server ak shell -c \
  "from authentik.core.models import User, Group; \
   u, created = User.objects.get_or_create( \
     email='<operator-email>', \
     defaults={'username': '<operator-username>', 'name': '<operator-name>', 'is_active': True}); \
   Group.objects.get(name='cf-agent-default').users.add(u); \
   print('created' if created else 'exists', u.username)"

# 2. 在 backend 端 pre-create User row（§5.7.2.b）
docker compose exec api python -m app.cli provision-operator \
  --email <operator-email> --name <operator-name>
```

如此 operator 第一次按「使用 Google 登入」就 end-to-end 通到 Dashboard，無中段 permission deny。

##### 5.7.3.c 為什麼這步沒做進 `provision-operator` CLI

評估過、不做。原因：

1. **CLI 跑時 Authentik user 通常還不存在**（主路徑是 first-login 才 enroll），CLI 此時無對象可 `g.users.add(u)`；強塞會變成「先在 Authentik 端 create 再加 group」，等同要把 CLI 從「補 backend row」擴張到「同時管 Authentik directory」，scope 失控。
2. **CLI 加 group 需要 Authentik admin API token**（或 `ak shell` exec），等於要把 `AUTHENTIK_API_TOKEN` 灌進 `api` container env，新增一條 prod secret 路徑只為了一個 break-glass CLI，不划算。
3. **真正的「next operator 自動進 group」是上游機制問題**（OAuth Source / enrollment flow auto-bind to group），不是 CLI 的責任邊界。M3.5 ship 後若 operator 數量上升，再開單把這個自動化做進 enrollment flow blueprint（候選機制：source post-enrollment policy binding，或 enrollment flow 內加 group-add stage）。本單先保證 runbook 完整。

退而求其次：`provision-operator` CLI 完成時在 stdout 印一條提醒，指 operator 回來看本節（§5.7.3.a）。

### 5.8 Backup / disaster recovery

Phase 1 不寫 automated backup；§5 setup 走過一遍要 1 小時，DB 倒掉重設 = 1 小時 + 把 `.env` 內 secrets 拷回 1Password。

M3.5 ship 前 backup 流程進 `operations.md`，包含：
- `docker run --rm -v authentik_postgres_data:/source -v $(pwd):/backup postgres:16-alpine pg_dump ...`
- `authentik_certs` named volume 一併備份（OIDC signing key；倒掉 = 所有 token 立即失效）
- 還原步驟驗證

### 5.9 Setup checklist

- [ ] §5.1 admin 首登完成、akadmin 密碼進 1Password
- [ ] §5.2 Google OAuth Source 設好（含 Authentication flow + Enrollment flow）；登出 admin 後 login 頁有 Google 按鈕；用 Workspace 帳號登入成功
- [ ] §5.2 T-078 fix：`default-source-authentication.authentication = none` 已套用。**Apply path 依環境而異**——dev：`ak shell` 改一次；CI / e2e：自動套用（`cf-e2e-bootstrap.yaml` 由 `docker-compose.test.yml` 掛起來的 blueprint upsert）；**prod：不在 e2e blueprint 範圍**，setup 時必 admin-UI 改 `default-source-authentication.authentication`（或在 prod 自己的 blueprint dir codify 一條 same shape 的 `authentik_flows.flow` upsert）。驗收：logout 後同瀏覽器再 Google 登入可達 Dashboard、不撞 "Flow does not apply"
- [ ] §5.2.1 `cf-google-init` blueprint 有 apply（`BlueprintInstance.status == successful`）；flow executor 回 `xak-flow-redirect` → `/oauth/source/oauth/login/google/`；真人 operator 首登 redirect 回 SPA（不落在 `/if/user/`）
- [ ] §5.2.1b `cf-builtin-flow-hardening` blueprint 有 apply（`BlueprintInstance.status == successful`，T-079）。**Apply path 依環境而異**——dev：`docker-compose.override.yml` 已 codify 單檔 mount，`docker compose up -d authentik-server authentik-worker` 即套；CI / e2e：自動套用（`docker-compose.test.yml` 整個 dir mount）；**prod：不在 prod compose mount 範圍**，setup 時必把 `infra/authentik/blueprints/cf-builtin-flow-hardening.yaml` 同 cf-google-init.yaml 一起 codify 進 prod blueprint dir（或在 prod admin UI 手動建：4 條 flow × 加 PolicyBinding，2 條 source flow 加 `policy_engine_mode: all`，詳見 §5.2.1b 的綁定表）。驗收：對 4 條內建 flow 跑 §5.2.1b verification 同樣的 8 條 curl，evil 變體（含 `/\evil.com` 給 `default-source-pre-authentication`）全回 `ak-stage-access-denied` 或同效
- [ ] §5.3 5 條 scope（`character:read` / `character:write` / `task:read` / `task:cancel` / `usage:read`）都建好，name 逐字對齊
- [ ] §5.4 5 個 application（1 SPA + 4 agent）+ 對應 provider 都建好；client_id 逐字對齊 `app/auth/mcp_clients.py`
- [ ] §5.4 **T-089 `character-foundry-mcp`**（第 6 個 app，真人 MCP delegated discovery）建好：public PKCE、explicit-consent、no `offline_access`、redirect_uris（loopback regex + claude.ai connector）、綁 `cf-agent-default` group。issuer 加進 `AUTHENTIK_ISSUER_URL`、client_id 加進 `AUTHENTIK_AUDIENCE`。**Apply path 依環境而異**——e2e/CI：`cf-e2e-bootstrap.yaml` 自動套；dev / prod：admin-UI（本節步驟）。驗收：`curl <host>/.well-known/oauth-protected-resource` 回合法 RFC 9728（`authorization_servers` 指向此 app issuer）；無 token 打 `/mcp/` 回 `401 + WWW-Authenticate`；MCP Inspector OAuth 模式 end-to-end auto-login（AC #3，manual）
- [ ] §5.5 group + policy binding：delegated 4 個綁 `cf-agent-default`、`cf-test-agent` 綁 `cf-test-agent-full`
- [ ] §5.6.1 Google login flow 通
- [ ] §5.6.2 `curl` client_credentials 拿到 access token、scope claim 含 5 條
- [ ] §5.6.3 access_token 是 JWT 格式（3 段 base64URL）
- [ ] §5.7 真人 operator 兩層 user 都備妥：首次 Google 登入自動 enroll Authentik user + backend row（T-071 後預設由 first-login auto-provisioning 處理；要 pre-provision 才跑 `provision-operator` CLI）
- [ ] §5.7.2.a auto-provisioning guardrail：`OAUTH_AUTO_PROVISION_ALLOWED_DOMAINS` 設成 Workspace tenant domain（例：`character-foundry.com`）；未設則所有 first-login 維持 401，operator 必須 `provision-operator` CLI 手動補
- [ ] §5.7.3 operator 已加進 `cf-agent-default` group（first-login 後 admin 跑 `ak shell` snippet，或 pre-provision 時順手加）；驗收：operator 在 SPA 走完「使用 Google 登入」可達 Dashboard、不被 `/oauth/application/o/authorize/` 用 "Permission denied" 擋
- [ ] `cf-test-agent` client_secret 進 1Password，未進 `.env.example`
