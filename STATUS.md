# Character Foundry — Implementation Status

> **Last updated:** 2026-05-19 — T-083 done (Sprint 3.5b Wave A continues). New planning doc `planning/agent-interface/endpoint-mcp-mapping.md` enumerates every `api-shape.md §2 / §5` endpoint with ✅ / ❌ MCP decision, tool name, packaging, scope, M3-vs-M4 status, and owning Wave B ticket. Bidirectional links added in `api-shape.md §5` header and `oauth-mcp-integration.md §3.3`. **Drift surfaced in §6 (3 items for user review before Wave B starts):** (a) `GET /v1/checkpoints/{id}` exists in code but missing from api-shape §5.2 — mapped to `character.get_checkpoint` 1:1, pushes T-084's tool count from "1 packaged + 8 CRUD = 9" to "1 packaged + 9 CRUD = 10"; (b) `POST /v1/characters/{id}/aliases/masks` exists in code but missing from api-shape §5.3 — bundled into `alias.add` inpaint mode (no new tool count, T-085 = 5 still); (c) Wave B miscellany (`task.cancel` / `task.list` / `task.get` / `prompt.preview` / `meta.get`) doesn't have a current owner ticket — recommend bundling into a "Wave B misc" mini-ticket or extending T-084. T-084 / T-085 / T-086 can now copy `bundles=[...]` verbatim from §3 of the new doc. — 2026-05-18 T-080 done (Sprint 3.5b Wave A start). MCP streamable HTTP server mounted at `/mcp` via `app.mount`, dual-stack JWT + Authentik OAuth resolved at the ASGI boundary into a `MCPAuthContext` contextvar (shared module: `app.mcp.auth`), per-tool scope enforcement via `require_mcp_scopes(SCOPE_CHARACTER_READ)` raised as ToolError with the same AgentError envelope `/v1/*` returns. **MCP error vs HTTP status** contract honoured — auth failures stay 200 with `CallToolResult.isError=True` JSON, not HTTP 401/403 (validated by `test_missing_token_surfaces_mcp_error` + `test_oauth_token_missing_scope_surfaces_mcp_error`). **Dispatcher pattern** (`app.mcp.app._MCPDispatcher`) decouples mount from FastMCP lifecycle — necessary because `StreamableHTTPSessionManager.run()` is single-use, so TestClient's per-test lifespan would crash on the second test's startup; dispatcher swap lets us rebuild a fresh FastMCP each lifespan while keeping the mounted ASGI callable stable. **Named scope constants** added to `app/auth/scopes.py` (`SCOPE_CHARACTER_READ` etc.) so tool declarations satisfy `test_oauth_scope_source_is_centralized` without backdooring the literal string. **`mcp>=1.27.0` pin** with PR #2038 link in pyproject. ⚠ PR #2038 has merged on `main` (2026-02-18) but **not yet in any release tag** as of v1.27.1 (2026-05-08): the installed `Context.report_progress` still omits `related_request_id`, so progress notifications would silently drop. Worked around in `app/mcp/tools/hello.py::_report_progress_with_request_id` (calls `send_progress_notification` directly with the explicit `related_request_id`); becomes a passthrough when the SDK fix releases. **Progress notification round-trip really observed** by `test_progress_notification_reaches_client` (the smoke test PR #2038 was designed to make robust — running over real streamable HTTP through `httpx.ASGITransport`, not in-memory). MCP transport security configured via `MCP_ALLOWED_HOSTS` / `MCP_ALLOWED_ORIGINS` env vars (defaults to FastMCP loopback allowlist); production behind nginx will need to allowlist the public host. **`hello.world` reply**: `f"hello, {client_id or 'jwt-user'}: {echo}"` (JWT path has no client_id so falls back to `jwt-user` to keep dual-stack reply deterministic). — Sprint 3.5b 開單（T-080 ~ T-087，8 張）：MCP server skeleton + tool registry + nginx /mcp proxy + endpoint mapping doc（Wave A，4 張全平行）+ character.create / alias.add / motion.generate packaged tool 與對應 CRUD wraps（Wave B，依 Wave A）+ Last-Event-ID resumability。對照 `planning/agent-interface/scope.md` §5.3 / open-questions Round 1 Q3 gotcha / `oauth-mcp-integration.md` §3 §5 / agent-interface Q9 deferred enumeration 全部落地進票。3.5c agent E2E smoke 暫不開（3.5b ship 完再開）。 — T-074 done：補 T-073 後 security review reveal 的 open-redirect 漏洞群。**Root cause** = Authentik core `_flow_done()`（`/authentik/flows/views/executor.py:380-393`）信 `PLAN_CONTEXT_REDIRECT` 不驗 host（顯式 comment 「we don't check if its an absolute URL or a relative one」），而 `SourceFlowManager._prepare_flow` 會把 attacker-controlled `SESSION_KEY_GET[next]` baked 進去。修法 = `infra/authentik/blueprints/cf-google-init.yaml` 加一條 ExpressionPolicy `cf-google-init-next-validation`：`urlparse(next).netloc.lower() == http_request.get_host().lower()`（case-insensitive per RFC 3986 §3.2.2）OR pure relative 才放行，否則 `_deny()` 把 `SESSION_KEY_GET` 清空 + `ak_message()` 拒；protocol-relative `//evil.com` / 反斜線變體在 urlparse 前 explicit-reject。**Dual binding（security review 出來的）**：target=flow（plan-time 主要路徑，raise `FlowNonApplicableException` 顯示 `ak_message`） + target=FlowStageBinding（race protection；FlowStageBinding 也加 `evaluate_on_plan: true` + `re_evaluate_policies: true`）。**為什麼要兩條 binding** = security-engineer subagent flag 一條真實 race：executor.py:179 `request.session[SESSION_KEY_GET] = get_params` 在 `if not self.plan` 之前**無條件**執行，attacker 用 legit `next` 起一條 plan、第二次 hit 用 evil `next` 會只覆蓋 session 不重 plan、policy 不重跑 → cached plan 走完 RedirectStage → callback `_prepare_flow` baked 新的 evil `next` → bypass。`re_evaluate_policies` + `ReevaluateMarker` 強制 stage 執行前再跑 policy。**為什麼 `_deny()` 要清 SESSION_KEY_GET**（不只是回 False）= 第二條 attack surface：policy 拒了 stage 後 plan 空 → executor 呼 `_flow_done()` → fallback 讀 `SESSION_KEY_GET[next]` 自己 `redirect_with_qs(next)`，只要 `is_url_absolute(next)` False。`/\evil.com`（netloc 空 → False）瀏覽器當 `//evil.com`、`javascript:alert(1)` 撞 `NoReverseMatch` 500。清掉 session → fallback 用 hardcoded `authentik_core:root-redirect` → 安全。**Trust invariants（policy 安全度依賴）**：`get_host()` 回 nginx `$host`，要 (a) prod `ALLOWED_HOSTS` 顯式 pin（不能 `*`）、(b) nginx strip 進來的 `X-Forwarded-*`、(c) `USE_X_FORWARDED_HOST` 不要被打開。任一被打破 = policy collapse；planning doc §5.2.1b 寫進 deploy 前必驗。**Curl verification 7+1+3 條全綠**：7 條原始（relative pass、同 host absolute pass、no-next pass、`https://evil.com` block、`//evil.com` block、`/\evil.com` block、`javascript:alert(1)` block）+ defensive 3 條（`/path//evil.com` pass、`http://LOCALHOST/...` pass case-insensitive、`http://localhost@evil.com/path` userinfo trick block）+ race 3 條（hit 1 legit pass、hit 2 evil 同 session 落 `/oauth/` safe、hit 3 backslash 同 session block）。**Scope 只綁 `cf-google-init`、不綁內建 flow** = `cf-google-init` 是專案 codified launcher、blast radius 局限在 SPA 攻擊鏈；綁 `default-authentication-flow` / `default-source-authentication` / `default-source-enrollment` 會改 Authentik 內建 flow 對所有 future use case 行為，需獨立 validation pass、開 **T-079**（已新增、post-3.5a backlog）追蹤。Planning：`authentik-stack.md` §5.2.1 加 T-074 retrofit note + 新增 §5.2.1b 完整章節（含 8 條 curl verification 含 race + path-double-slash + trust invariants）。 — 2026-05-18 T-077 done：補上 §5.7.3 operator group membership（`cf-agent-default`），就是 T-076 CDP reveal 的 wall 5。落地三件：(a) `planning/devops/authentik-stack.md` §5.7 加 §5.7.3，拆 5.7.3.a「first-login 後手動加 group（主路徑，搭配 T-071）」/ 5.7.3.b「pre-provision 時順手加（旁路）」/ 5.7.3.c「為什麼沒做進 CLI」（CLI 跑時 Authentik user 通常還不存在，要塞進 CLI 就得擴張成「同時管 Authentik directory」，scope 失控；真要 next-operator 自動化是 OAuth Source / enrollment flow 上游機制，留給 M3.5 ship 後 operator 數量上升時再開單）；(b) §5.9 checklist 加一條 group-membership 驗收；(c) `api/app/cli.py::_run_provision_operator` 完成時印一條「T-077: next step」提醒（含 `cf-agent-default` + `§5.7.3` reference），CLI 不嘗試呼 Authentik API（會擴張 secret 路徑）。新 regression test `tests/cli/test_provision_operator.py::test_provision_operator_prints_group_membership_reminder` 用 `capsys` 釘住 stdout 含 `cf-agent-default` + `§5.7.3` —— 防止後人靜默砍掉那行提醒重新製造 wall 5。⚠ 主路徑（T-071 first-login auto-provisioning）這道牆**沒消** —— PolicyBinding 卡的是 Authentik-side group，T-071 補的是 backend row，兩層獨立；T-077 把它寫進 runbook 讓 admin 知道首登後要跑 `ak shell` snippet 補 group，不嘗試在這單做上游 auto-bind。dev 環境之前手動加過，本單沒改 dev state。 — 2026-05-18 T-071 done：first-login auto-provisioning。`_resolve_oauth` 在 Authentik 驗過的 delegated token 沒 backend `users` row 時，自動建一個（display name 用 OIDC `name` claim，退到 email local part），把 `provision-operator` CLI 從「唯一補 row 方法」退回 break-glass / pre-provision 角色。Guardrail = 新 env var `OAUTH_AUTO_PROVISION_ALLOWED_DOMAINS`（comma-separated email-domain allowlist；未設則 fail-closed，等同 T-071 前的 401 行為）—— 與 §5.2 Google OAuth Source `hd=` gate 形成 defense in depth，防止 Authentik 端 misconfig 讓任意 verified Google 帳號在 DB 長 row。Race 處理：兩個 first-login 同時撞 unique constraint 時 catch IntegrityError 後 re-select。auto-provision 走獨立短命 `AsyncSession` 不污染 request transaction。順手把 `test_dual_stack.py` 裡的 OAuth fixture（`_oauth_env` / `_preload_jwks_cache` / `make_oauth_token` / RSA keypair / JWKS doc）搬進 `tests/auth/conftest.py`，讓新 `test_oauth_auto_provisioning.py` 可重用。Planning 更新：`authentik-stack.md` §5.7.2 拆成 5.7.2.a（自動 path，主路徑）/ §5.7.2.b（CLI，break-glass）；§5.9 checklist 補 env var；`environment-variables.md` 加 §2.8a。⚠ 既有 dev `.env` 要手動加 `OAUTH_AUTO_PROVISION_ALLOWED_DOMAINS=character-foundry.com`（或對應 Workspace domain）+ `docker compose up -d api` 才會吃到；不設則 dev first-login 維持 401。 — 2026-05-15 T-078 done：修 wall 6（已 provision 好的 operator 登出後無法 re-login）。**Root cause 跟 ticket 的三條候選都不完全對上** —— ticket 假設「SPA logout 也要結束 Authentik session」(a)、「放寬 `default-source-authentication`」(b)、「source-init 帶 `prompt=login`」(c)。實作走 (b)：把 `default-source-authentication.authentication` 從 Authentik 出廠的 `require_unauthenticated` 改成 `none`。理由是 (a) 的兩個變形（navigate 到 `default-invalidation-flow` / OIDC `end_session_endpoint`）CDP 實測都失敗 —— 前者的 `UserLogoutStage` 會 `auth_logout()` flush 整個 session 把 `SESSION_KEY_GET[next]` 一起 nuke，`_flow_done()` 沒 next 可 honor 回去 SPA，bounce 到 `default-authentication-flow` 卡住；後者的 `default-provider-invalidation-flow` 在這套 Authentik 出廠就沒 stage bindings、flow executor 永遠卡 `ak-loading`，換成有 `UserLogoutStage` 的 `default-invalidation-flow` 又遇 `_flow_done` 把 post-logout redirect 用相對路徑下發、browser 把它解析到 Authentik origin（dev 從 `:5173` flip 到 `:80`，UX 違反「logout 落回 SPA `/login`」）。(c) 結構上錯——block 點是 source-auth flow 的 `require_unauthenticated`、不是 upstream IdP，`prompt=login` 救不到。落地 (b)：SPA 端**零 code 改動**（純註釋）；codify 改在 `infra/authentik/blueprints/cf-e2e-bootstrap.yaml` upsert `default-source-authentication.authentication=none`（**只蓋 CI / e2e** —— 這支 blueprint 是 `docker-compose.test.yml` 掛起來的，dev `override.yml` 只掛 `cf-google-init.yaml`、prod 沒有對應 mount）；dev 已 `ak shell` 同步；prod setup 時必 admin-UI 或自己 codify 一條 prod-blueprint 補上（§5.9 checklist 已列）。CDP 在 real Chrome（leoyeh906）走 fresh login → logout → re-login 全 silent 通過。Side effect: `cf-e2e-bootstrap.yaml` 加 e2e regression test（雖然 e2e 走 password path 跑 `default-authentication-flow`，本來就 `auth=none`，但仍是 logout flow 的 smoke gate）。⚠ 本 codebase 只用一條 OAuth Source（Workspace Google）；未來若加第二條且該 source 需要「拒絕已 authenticated」的功能語意，clone 一條 source-specific auth flow，不要把這條再鎖回 `require_unauthenticated`。 — T-076 done（PR #101）：修 wall 4 —— dev `:5173` 下 Authentik flow interface 的 bootstrap XHR 跨來源（`:5173` → `:80`）被 CORS 擋、卡 `Loading…`。Root cause：flow interface 用 Authentik 絕對 `base_url`（`http://localhost/oauth/api/...`，因 nginx `$host` 去 port）發 XHR，跟 `:5173` SPA 跨來源。修法（候選 1 最小形式）：`VITE_AUTHENTIK_AUTHORIZE_URL` 改**絕對** → SPA 的 Google / 帳密兩個登入入口都直接導航到 Authentik 真實 origin `:80`，flow interface + 它的 XHR 同源、無 CORS；`redirect_uri` 仍 `:5173` 把人帶回 SPA；`TOKEN/LOGOUT_URL` 維持相對（`fetch` 同源）。**零前端 code 改動**（`buildAuthorizeUrl`/`buildSourceInitUrl`/帳密 path 都已能吃絕對 URL），只改 `.env.example` + 註解（`vite.config.ts`、`authentik-stack.md` §5.2.1a）。CI `pr.yml` 自己寫 `.env`、維持相對（CI 單源不受影響）。CDP run `r2` 驗證 fresh-session Google 登入 end-to-end 走到 Dashboard（`:5173/`、heading 我的角色）。**CDP 驗證連環 reveal 下游兩道牆，已開單**：wall 5 = T-077（operator 不在 `cf-agent-default` group → authorize endpoint 擋；§5.7 runbook 缺口；dev 已手動補）、wall 6 = T-078（logout 後 SPA 不結束 Authentik session → re-login 撞 `require_unauthenticated`；T-073 早預告、使用者實測確認；真功能 bug 優先級高）。⚠ 既有 dev `.env` 要手動把 `VITE_AUTHENTIK_AUTHORIZE_URL` 改絕對 + `docker compose up -d web`（`.env` 是 gitignored，本單只改 committed 的 `.env.example`）。 — T-075 done（PR #100）：修 T-073 ship 的 encoding regression。T-073 把 SPA URL 包成 `/oauth/if/flow/cf-google-init/?query=next=X`，但 flow **interface** 前端（`FlowInterface-2024.12.5.js`）會自己把 `window.location.search` bundle 進 executor API 的 `?query=` —— 多包一層 → executor `QueryDict` 出 `{query: "next=X"}` 沒有 `next` key → `_prepare_flow` 還是 fallback `/if/user/`。T-073 的 curl 驗證會過是因為它直接打 executor **API**（那層才要 `?query=`）；SPA 打的是 **interface**（吃 plain `?next=`、前端自己 bundle）。修法：`buildSourceInitUrl` 改產 plain `/oauth/if/flow/cf-google-init/?next=X`。CDP 驗證確認 encoding 修對（network log 看到正確的 executor 呼叫）。**但 CDP 同時 reveal wall 4** —— flow interface 的 bootstrap XHR 打 Authentik 絕對 `base_url`（`http://localhost/oauth/api/...`）跟 SPA origin `:5173` 跨來源 → CORS 擋 → 卡 `Loading…`；dev-`:5173`-only（prod/e2e 同源無此問題），拆 **T-076**。使用者拍板「先 ship T-075、再做 T-076」。⚠ 另一個踩過的坑：前幾輪 CDP 測試打到 stale pre-T-073 SPA code —— dev `web` 的 Vite file-watcher 沒抓到 Windows→Docker bind-mount 變更，要 `docker compose restart web` 讓它 cold-start 重掃。 — 2026-05-14 T-073 landed: 修 operator 首登 `next`-redirect 缺口（wall 3）。**Root cause 跟 ticket 假設不同** —— 不是 enrollment flow 設定問題，是 Authentik 2024.12.5 的 OAuth `OAuthRedirect` view（`sources/oauth/views/redirect.py`）**靜默忽略 `?next=`**：`SESSION_KEY_GET` 只由 flow-executor 的 `dispatch()` 寫入，bare source-init path 從不寫，所以 callback 回到 `_prepare_flow` 時 `final_redirect` fallback 到 `/if/user/`。影響 **enrollment + authentication 兩條 flow**（ticket 原假設「auth honor、enroll 不 honor」是錯的，兩條都走 `_prepare_flow`）。修法：新增 `cf-google-init` launcher flow（單一 RedirectStage → `/oauth/source/oauth/login/google/`），codify 在 `infra/authentik/blueprints/cf-google-init.yaml`；SPA `buildSourceInitUrl` 改導到 `/oauth/if/flow/cf-google-init/?query=next=...` 而非 bare source-init。blueprint dev 由 `docker-compose.override.yml` 單檔 mount、e2e 由 `docker-compose.test.yml` dir mount 共用。**Ticket 的「E2E gate N/A、不碰 SPA code」判定已修正** —— fix 確實需要 ~1 行 SPA 改動（`buildSourceInitUrl` 的 URL builder），e2e spec 同步更新成覆蓋 SPA→flow→source-init chain。驗證（automated）：blueprint apply 乾淨（`status=successful`）、flow executor 在 live session 寫入 `authentik/flows/get={next:<authorize URL>}`（= `_prepare_flow` 讀的那把 key）並回 `xak-flow-redirect` → source-init；完整 Google round-trip（AC #4）需真 Google 帳號，是 ticket 自己標記的「Manual」operator step。順手修 STATUS stale：T-066 之前標 TODO 但檔案已在 `tickets/DONE/`。 — T-070 landed: `web/vite.config.ts` dev proxy 補 `/oauth/` entry（target `http://nginx`、不 rewrite、`changeOrigin: false`）+ `/api` target 改 `http://api:8000`。`changeOrigin` 是這單的關鍵 deviation：ticket 原建議 `true`，CDP 驗證發現 `true` 會把 `Host` 改寫成 `nginx` → Authentik 用它拼出 `redirect_uri=http://nginx/...` → Google 直接 reject；`false` 保留瀏覽器真實 Host（nginx `$host` 去 port → `redirect_uri=http://localhost/...`，Google 收）。驗證走 CDP 連本機真實 Chrome 跑 end-to-end：`:5173/login` → vite proxy → nginx → Authentik source-init → Google（發出真 auth code）→ callback 回 Authentik，proxy hop 全通、無 bounce 回 `/login` —— proxy fix 本身完整驗證。CDP 測試另連環撞三道 operator-config wall（皆非 T-070 code scope）：wall 1 OAuth Source 沒設 enrollment flow（T-069 已文件化，本次 dev 用 `ak shell` 補上 `default-source-authentication` / `default-source-enrollment`）、wall 2 backend 無 `User` row（`provision-operator` CLI 補上 `leoyeh906@gmail.com`）、**wall 3 enrollment flow 完成不 redirect 回 SPA、落在 `/if/user/`，且 `default-source-authentication` 的 `require_unauthenticated` 讓重試撞 "Flow does not apply"** —— wall 3 是 T-069 runbook 沒涵蓋的真缺口。三道 wall 開了 T-071（backend OAuth auto-provisioning，落地 `authentik-stack.md` §5.7.2 留的 M3.5b deferred item）、T-072（nginx `/api/health` docker 內網 502，T-070 topology 驗證 reveal）、T-073（Authentik source enrollment `next`-redirect 缺口，operator-amendment）。 — 同日稍早 T-069 implemented：補上 T-053 §5.2 留的 dev operator provisioning 設定缺口（`authentik-stack.md` §5.2 補 OAuth Source 的 Authentication / Enrollment flow、新增 §5.7「Provision a dev operator」、`provision-operator` CLI 帶隨機不記錄 hash 把「OAuth-only」做進結構）。
> **Phase:** Sprint 1 done（T-006 ~ T-012 全部 done，M1 達成）；Sprint 2 done（T-013 ~ T-028 全部 done，M2 達成）；**Sprint 3 done（T-029 ~ T-041，13 張全部 done，M3 達成）**

---

## Current state

**Planning phase：** ✅ 完成（product / ux / data / backend / frontend / devops 全收斂）
**Implementation phase：** 尚未開工

---

## Sprint progress

### Sprint 0 — Infrastructure
**目標：** `docker compose up` 能跑起整套 stack，hello world 有回應。

| # | Ticket | Status |
|---|---|---|
| T-001 | Repo scaffolding | DONE |
| T-002 | Alembic + initial migrations (teams, users) | DONE |
| T-003 | Remaining migrations (characters → tasks) | DONE |
| T-004 | CI workflow (PR checks) | DONE |
| T-005 | StorageBackend interface + LocalFilesystemBackend | DONE |

### Sprint 1 — Auth + App Shell
**目標：** Login 能成功，看到空 Dashboard。

| # | Ticket | Status |
|---|---|---|
| T-006 | Backend auth (JWT login/refresh/logout/me) | DONE |
| T-007 | Frontend scaffolding (Vite + shadcn init) | DONE |
| T-008 | Frontend auth (login page + store + guard) | DONE |
| T-009 | Backend /health + /v1/meta | DONE |
| T-010 | Frontend TopNav + DegradedBanner | DONE |
| T-011 | Frontend Toast + ErrorBoundary | DONE |
| T-012 | E2E smoke test (login flow) | DONE |

### Sprint 2 — Character Creation
**目標：** 建 Character、選單 / 參考圖模式、Checkpoints、確立 Base（M2）。

| # | Ticket | Status |
|---|---|---|
| T-013 | Backend task queue (arq + Redis) + Task API | DONE |
| T-014 | Backend AI client infra (gpt-image-2 + circuit breaker + stub) | DONE |
| T-015 | Backend Prompt Reconciler module (gpt-5-mini) | DONE |
| T-016 | Backend Character CRUD + CreationSession bootstrap | DONE |
| T-017 | Backend Checkpoint generation flow | DONE |
| T-018 | Backend Select Base / Fork / Abandon | DONE |
| T-019 | Backend Prompt preview endpoint | DONE |
| T-020 | Frontend Dashboard (grid + empty state) | DONE |
| T-021 | Frontend New Character page (mode picker) | DONE |
| T-022 | Frontend Creation Session — template mode | DONE |
| T-023 | Frontend Creation Session — reference mode | DONE |
| T-024 | Frontend Prompt preview modal (M-01) | DONE |
| T-025 | Frontend Select Base + Character Detail (Base only) | DONE |
| T-026 | E2E Character creation smoke test (template) | DONE |
| T-027 | CharacterDetail DTO + frontend resume in-progress session | DONE |
| T-028 | Worker post-lock checkpoint guard（從 T-018 PR #23 拆出來，Codex round-2 P1） | DONE |

### Sprint 3 — Aliases + Motions
**目標：** 三合一 Alias 輸入（含 Inpaint）、Preset + Custom motion，跑完 M3 milestone。

| # | Ticket | Status |
|---|---|---|
| T-029 | Backend Veo 3.1 i2v client + stub | DONE |
| T-030 | Backend gpt-image-2 image2image + inpaint extension | DONE |
| T-031 | Backend Alias generation endpoint + worker | DONE |
| T-032 | Backend Alias list / detail / rename / delete | DONE |
| T-033 | Backend Motion generation endpoint + worker | DONE |
| T-034 | Backend Motion list / detail / rename / delete | DONE |
| T-035 | Backend Prompt preview extension（alias / motion mode + MaskInput schema）| DONE |
| T-036 | Frontend Alias edit page (P-06) + InpaintCanvas | DONE |
| T-037 | Frontend Character Detail aliases + motions sections | DONE |
| T-038 | Frontend Motion preset generation（click-to-generate + SSE）| DONE |
| T-039 | Frontend Custom motion modal (M-02) | DONE |
| T-040 | Frontend Prompt preview modal extension（alias / motion mode）| DONE |
| T-041 | E2E Alias creation + motion preset smoke（M3 gate）| DONE |
| T-042 | Fix gpt-image API contract on real provider（drop dall-e-3 params + multi-image `image[]`） | DONE |
| T-043 | Sync `planning/backend/ai-integration.md` to real gpt-image contract（T-042 follow-up） | SUPERSEDED by T-048 |
| T-044 | Outgoing-body contract test for gpt-image client（T-042 follow-up） | DONE |
| T-045 | Fix reconciler client for gpt-5-mini contract drift（max_completion_tokens + drop temperature=0）| DONE |
| T-046 | Shared `/storage` volume + nginx `/storage/` proxy（image preview broken bug）| DONE |
| T-047 | Aspect-ratio dropdown + framing guidance（head cropping fix）| DONE |
| T-048 | Sync planning docs（T-042 / T-045 / T-046 / T-047）+ yaml bind-mount in dev override | DONE |
| T-049 | Require e2e happy path for routing / new-page / critical-action PRs（process gate）| DONE |
| T-050 | Reconciler prompt tuning vs OpenAI image-gen cookbook（gpt-image only；i2v 之後另開單） | DONE |
| T-051 | Veo 3.1 RAI filter 偵測 + 修 `model_invalid_request` template 誤導性「returned 4xx」字串 | DONE |

**Dependency / parallelization plan：** 見 `tickets/PARALLEL_WORKFLOW.md`。Wave A（T-029 / T-030 / T-035 / T-036 / T-040）可立即平行開工。

### Sprint 4 — Download + Usage（尚未開單）
ZIP 匯出、Copy Character、Usage dashboard。

### Sprint 5 — Polish（尚未開單）
剩餘錯誤處理、E2E coverage、效能調整。

### Sprint 3.5 — Agent-native baseline（plan phase 完成 2026-05-07，3.5a 已開單）
**目標：** OAuth 2.1（替換 JWT）+ MCP server，外部 agent 不看 REST 文件就能跑全流程。
**規劃：** ✅ 4-step plan phase 全部完成（2026-05-07）。

> **2026-05-12 sequencing 決定（使用者）：** Sprint 3.5a OAuth 系列**整體 blocked on Sprint 3.5-pre harness 全完成**。Harness 蓋完才開始做 M3.5——避免 OAuth + MCP 兩個新 layer 在沒 guardrail 的狀態下落地。詳見 `planning/harness/`。

#### Sprint 3.5-pre — Harness pre-flight（已開單 2026-05-12，未動工）

對照 Martin Fowler "Harness Engineering for Coding Agents"，由 Harness Agent 規劃。完整 rationale 見 `planning/harness/roadmap.md`。

| # | Ticket | Status |
|---|---|---|
| T-058 | 真 provider contract replay sensor（A1；manual-only since T-066）| DONE |
| T-059 | Architecture fitness — layering / import-direction test（A2）| DONE |
| T-060 | Coverage gate + mutation testing on critical modules（A3）| DONE |
| T-061 | Secret scan + SAST baseline（A4；**T-053 之前必 land**）| DONE |
| T-062 | Subagent stack — security-engineer + db-optimizer（A5）| DONE |
| T-063 | `CF_SKIP_REVIEW=1` audit log（A6）| DONE |

**Dependency / parallelization：**
- T-058 / T-059 / T-060 / T-062 / T-063 五張無內部 dep，可全 wave 平行
- T-061 也無內部 dep，但**對下游 T-053 是 hard blocker**
- 全部 land 後才解 Sprint 3.5a OAuth 系列的 sequencing block

#### Sprint 3.5a — OAuth migration（已開單，未動工；blocked on Sprint 3.5-pre）

| # | Ticket | Status |
|---|---|---|
| T-052 | Authentik docker service 加入 stack | DONE |
| T-053 | Authentik 設定 Google upstream IdP + client 註冊 | DONE |
| T-054 | Backend dual-stack auth middleware（JWT + OAuth） | DONE |
| T-055 | `refresh_token` table 加 `token_source` 欄位 | DONE |
| T-056 | Frontend Sign in with Google + AuthCallbackPage + authStore dual-stack | DONE |
| T-057 | E2E OAuth login smoke + dual-stack 並存測試（ship gate） | DONE |

**Dependency / parallelization：**
- 整個 Sprint 3.5a blocked on Sprint 3.5-pre 全完成（2026-05-12 決定）
- 解 block 後：T-052 / T-055 可平行起步（無內部 dep）
- T-053 等 T-052 **且** T-061（A4 secret scan）已 merge — ✅ both gates met before T-053 land；T-054 等 T-055 + T-053
- T-056 等 T-054；T-057 等 T-056

#### Harness B-tier follow-ups（M3.5 ship 後再排；可隨時插單，不 block Sprint 3.5a）

| # | Ticket | Status |
|---|---|---|
| T-064 | Provider-drift issue dedup by failure signature（T-058 round-3 defer；T-066 後 priority 下調）| TODO |
| T-065 | PR CI guard — `[tool.mutmut]` change must bump `.harness/mutation-baseline.json`（T-060 enforcement upgrade）| TODO |
| T-066 | Provider contract replay 改 manual-only（停 nightly cron，~$10/月成本砍）| DONE |
| T-067 | Harden docker-compose secret interpolation + minimal container posture（T-052 PR #85 Codex P1 + security review batch defer）| TODO |

#### Post-3.5a UX follow-ups（不 block M3.5 ship；setup / dev 流程 reveal 的小調整）

| # | Ticket | Status |
|---|---|---|
| T-068 | SPA login page — Google direct shortcut + 帳密 fallback + dev escape hatch（推翻 `planning/frontend/oauth-integration.md` §1.1 單按鈕決策）| DONE |
| T-069 | Dev operator provisioning — 修 `authentik-stack.md` §5.2 漏設 OAuth Source flow + 補真人 operator 兩層 user（Authentik enrollment + backend `User` row）的設定步驟（T-068 dev 測試 reveal 的 setup 缺口）| DONE |
| T-070 | Vite dev server `/oauth/` proxy — OAuth 登入在 `localhost:5173` 壞掉：`web/vite.config.ts` dev proxy 只 proxy `/api`（且 target 指不到 containerized `pnpm dev` 的 api），沒有 `/oauth/` proxy → SPA 的 relative `/oauth/...` navigation 撞 Vite SPA fallback、bounce 回 `/login`。CI 看不到（e2e 走 nginx:80 同源）。T-068 dev 測試 reveal | DONE |
| T-071 | Backend OAuth auto-provisioning — `_resolve_oauth` 第一次拿到有效 Authentik token 時自動建 backend `User` row（+ allowlist / `hd=` guardrail），取代手動 `provision-operator` CLI。落地 `authentik-stack.md` §5.7.2 留的 M3.5b deferred item。T-070 dev 測試 wall 2 reveal | DONE |
| T-072 | nginx `/api/health` docker 內網 502 — `http://nginx/api/health` 從 network 內回 502（e2e 走 nginx:80 真實路由綠，疑為 `/health` path-specific 小問題）。T-070 dev-proxy topology 驗證 reveal | TODO |
| T-073 | Authentik source enrollment `next`-redirect 缺口 — 真人 operator 首登走 enrollment flow 完成後落在 `/if/user/`、不 redirect 回 SPA，重試又撞 `require_unauthenticated` 的 "Flow does not apply"。operator-amendment（補 `authentik-stack.md` §5.2）。T-070 dev 測試 wall 3 reveal | DONE |
| T-074 | Authentik flow-executor `next` open-redirect — `?query=next=https://evil.com` 在登入完成後會被 redirect 出站；Authentik core `_flow_done` 的 `PLAN_CONTEXT_REDIRECT` path 不驗證 `next`。既有行為、每條 flow-executor URL 都有，非 T-073 引入。修法：綁 expression policy 驗證 same-origin。T-073 security review defer | DONE |
| T-075 | T-073 regression — `buildSourceInitUrl` 把 `next` 包成 `?query=next=` 多包一層；flow interface 前端會自己把 `location.search` bundle 進 executor 的 `?query=`，結果 executor 拿到 `{query: "next=X"}` 沒有 `next` key → `_prepare_flow` 還是 fallback `/if/user/`。修法：改產 plain `?next=`。T-073 AC#4 CDP 測試抓到 | DONE |
| T-076 | flow interface XHR CORS（dev `:5173`）— `cf-google-init` flow interface 載得起來、executor 呼叫格式也對（T-075 已修），但 interface 的 bootstrap XHR 打 Authentik 絕對 `base_url`（`http://localhost/oauth/api/...`）跟 SPA origin `:5173` 跨來源 → CORS 擋 → 卡 `Loading…`。dev-only（prod/e2e 同源）。修法：`VITE_AUTHENTIK_AUTHORIZE_URL` 改絕對 → 導航直接到 `:80` 真實 origin。CDP 驗證 fresh-session Google 登入 end-to-end 到 Dashboard。T-075 CDP 驗證 reveal（wall 4）| DONE |
| T-077 | operator group provisioning 缺口（wall 5）— `Character Foundry SPA` application policy-bind `cf-agent-default` group，但 `authentik-stack.md` §5.7 的 operator-provisioning runbook 沒把 operator 加進去 → 新 operator 過了 Authentik 登入卻被 authorize endpoint "Permission denied"。dev 已手動補；runbook / CLI 缺口待修。T-076 CDP 驗證 reveal | DONE |
| T-078 | logout 後無法 re-login（wall 6）— SPA logout 只 revoke OAuth token、不結束 Authentik session → re-login 時 `default-source-authentication` 的 `require_unauthenticated` 判 "Flow does not apply" → 拒。T-073 早預告、T-076 後使用者實測確認。真功能 bug（連已 provision 的 operator 都中），優先級高於 T-077。| DONE |
| T-079 | Authentik built-in flow next 同 open-redirect — 把 T-074 落地的 `cf-google-init-next-validation` policy 延伸 binding 到 `default-authentication-flow` / `default-source-authentication` / `default-source-enrollment`，closing T-074「Not in scope」段註明的同一個 open-redirect class 在內建 flow 上的曝面。T-074 security review defer。 | TODO |

#### Sprint 3.5b — MCP server + 核心 packaged tool（已開單 2026-05-18，未動工）

對照 `planning/agent-interface/scope.md` §5.3「Sprint 3.5b = MCP server 骨架 + 4 個 M3-範圍核心 tool」，由 agent-interface + backend agent 規劃。

| # | Ticket | Status |
|---|---|---|
| T-080 | MCP server skeleton（FastAPI sub-app `/mcp` + Python SDK ≥ PR #2038 + dual-stack auth integration + `hello.world` smoke tool）| DONE |
| T-081 | MCP tool registry + 3 條 CI guardrails（scope coverage / tool scope consistency / allowlist consistency）| TODO |
| T-082 | nginx `/mcp` proxy + `proxy_read_timeout ≥ 180s`（streamable HTTP SSE 不被剪斷）| TODO |
| T-083 | api-shape §5 endpoint MCP review（whitelist / blacklist / packaging map，輸出 `planning/agent-interface/endpoint-mcp-mapping.md`）| DONE |
| T-084 | MCP tool `character.create`（packaged）+ character CRUD 1:1 wraps（9 個 tool = 1 packaged + 8 CRUD；M4-deferred 的 manifest / copy / export 不在本單，由 M4 ticket 從 day 1 帶）| TODO |
| T-085 | MCP tool `alias.add`（packaged）+ alias CRUD 1:1 wraps（5 個 tool）| TODO |
| T-086 | MCP tool `motion.generate`（packaged, polymorphic）+ motion CRUD 1:1 wraps（6 個 tool）| TODO |
| T-087 | MCP streamable HTTP `Last-Event-ID` resumability（i2v 長 task 斷線重連）| TODO |

**Dependency / parallelization：**
- **Wave A（foundation；4 張幾乎全平行）**：T-080 / T-082 / T-083 無內部 dep，可同時起步。T-081 主體（registry pattern + 3 條 CI script）也可並行開發，唯一耦合是 T-080 落地的 `hello.world` migrate 進 registry 的那 commit 必須等 T-080 merge 後追加（per T-081 Depends-on 段；Codex review #106 round-3 抓到原本「Depends on: none」與 migration 要求衝突已 reconcile）
- **Wave B（核心 tool；3 張依 Wave A）**：T-084 / T-085 / T-086 等 T-080 + T-081 + T-083 三張完成才開；T-084 先行作為 pattern reference 較順，但不是 hard dep
- **T-087**：等 T-080（transport 層）+ T-086（i2v 是最關鍵測試對象）

**Plan phase deliverable（M3.5 整體；3.5a 已 ship）：**
- `planning/agent-interface/open-questions.md` — Round 1/2/3 決策紀錄（9 條全鎖）
- `planning/auth/open-questions.md` — 決策紀錄（8 條全鎖）
- `planning/backend/oauth-mcp-integration.md` — scope decorator + MCP tool registry + CI 護欄
- `planning/frontend/oauth-integration.md` — login UI + authStore dual-stack
- `planning/devops/authentik-stack.md` — Authentik docker stack + persistence
- `tickets/_TEMPLATE.md` — 新增「OAuth scope required」+「MCP tool delta」section

**關鍵決策（high level）：**
- OAuth provider：Authentik (OSS) + Google Workspace 當 upstream IdP
- Grant types：delegation（Auth Code + PKCE）+ M2M（Client Credentials）並存
- Scope：5 條（`character:read/write` / `task:read/cancel` / `usage:read`）+ narrow default + per-client 覆寫
- Signed URL：維持獨立 JWT，與 OAuth 解耦
- MCP transport：streamable HTTP, same-process FastAPI sub-app `/mcp`
- Client 註冊：pre-registered allowlist（Figma 模式），DCR 不開
- Migration：簡化 dual-stack，1 sprint 完成

#### Sprint 3.5c — Agent E2E smoke（未開單；3.5b ship 完再開）

對照 `planning/agent-interface/scope.md` §5.3「Sprint 3.5c = 用一個外部 agent 跑完 §1 完成條件（登入 → 建 character → 確立 base → 加 alias → 生 motion）」。0.5 週估時。

---

## Milestones

- [ ] **M0** — Dev environment runs（`docker compose up` → `/health` returns ok）【Sprint 0 完成】
- [x] **M1** — Login works end-to-end【Sprint 1 完成】
- [x] **M2** — Create Character (template mode) end-to-end【Sprint 2 完成】
- [x] **M3** — Aliases + Motions working【Sprint 3 完成】
- [ ] **M3.5** — Agent-native baseline：OAuth 2.1 + MCP server，外部 agent 能不看 REST 文件跑全流程【2026-04-30 從 Phase 2 拉回 Phase 1；詳見 `planning/agent-interface/`、`planning/auth/`】
- [ ] **M4** — Download ZIP works【Sprint 4 完成】
- [ ] **M5** — First internal user feedback【Sprint 5 完成】

---

## 開新 ticket 時更新這張表

- 新單：加進對應 sprint 區塊
- Status 改：同步更新這張表的狀態欄
- 完成：移進 DONE（`git mv`）+ milestone 若符合就勾

---

## Known risks / deferred items

| # | Item | 處理時機 |
|---|---|---|
| M5 | Dropdown 選項實際內容 | 實作時平行填充 |
| M7 | 錯誤 UX 細節訊息 | Frontend 實作時對照真 backend 回應 |
| M8 | Lip sync 延後是未驗證的賭注 | Phase 1 demo 前做 5 人快速 check |
| FB-3 | Storage URL expired 時 backend 要回對的 code | ✅ T-005 完成（`STORAGE_URL_EXPIRED` vs `AUTH_INVALID_TOKEN` 已分開） |
| - | Visual design (Pencil mockup) | 之後需要再開 UX iteration 3 |
| S2-1 | Slug-based URL（目前 `/characters/:id`）| Sprint 3/4 衡量 SEO/可分享性需求再做 |
| S2-3 | Dashboard 分頁 / infinite scroll（T-020 首版用 `limit=100` 平鋪，未做 cursor pagination）| Character 數逼近 100 或 UX 反饋時 |
| S2-4 | `Checkpoint` DTO 不含 `menu_selections` / `freeform_note`，所以 server-loaded checkpoint 點 `[用這張再改]` 無法 prefill form（T-022 placeholder 期間靠 client-side 記憶；reload 後就只設 remix base、form 留白）| Backend 加欄位後 Frontend 移除 placeholder fallback |
| S2-6 | `BaseDTO` 缺 prompt 欄位（`menu_selections` / `freeform_note` / `prompt_summary`），所以 Character Detail 上的「查看完整 prompt」modal 只能顯示 source checkpoint id + 建立時間，沒辦法重現完整 prompt 組合。T-025 frontend 落地時用 `BasePromptModal` placeholder 暫頂；Backend 在 BaseDTO 加 prompt 欄位後即可改為 reuse PromptPreviewModal。| 開新 ticket 擴充 `BaseDTO` schema |
| S3-2 | T-030 `edit_image2image` 多參考圖的 multipart shape（重複 `image` field name）依 gpt-image-1 公開合約建模；gpt-image-2 假設沿用，但需在 T-031 整合真 provider 前以 smoke 驗證一次 | T-031 production cutover 前 |
| S3.5-1 | Route 層直接 import ORM models 的歷史 leak：4 條真 leak（`routes/characters.py` → Character / CreationSession / BaseAsset、`routes/tasks.py` → Task）+ 10 條 sanctioned User-as-auth-context（routes / deps via `Depends(get_current_user)`）。真 leak 改走 repository helper：`character_repo.get_character_by_id` 等已存在；`base` 與 `creation_session` 需在 `app/repositories/` 新增 `base_repo` / `creation_session_repo` 模組（schemas 已有 `app.schemas.creation_session` / `app.schemas.base`，repo 層補 thin wrapper 即可）。Sanctioned exception 需要 UserContext Pydantic schema 設計：handler 端目前用 `user.id` / `user.team_id`（grep `current_user.\b` 列當前實際使用面，schema 對齊那組欄位即可）。全部列在 `api/pyproject.toml` `[tool.importlinter]` 的 `ignore_imports`（T-059 標註好兩種類別與該怎麼修）。| 每張碰 characters / tasks route 的 ticket 順手清一條；UserContext refactor 開單時統一處理 sanctioned exception |
| S3.5-2 | `app/auth/*` 不在 mutmut scope：`tests/auth/conftest.py` lazy-imports `app.main` → ORM models → `pgvector` → numpy；mutmut 3.5 in-process trampoline 重 import 觸發 numpy 的 "cannot load module more than once" guard，`pytest-forked` 治標但會關掉 `mutate_only_covered_lines` 的 stats 蒐集（forked subprocess 不回報 trampoline hits）。原 T-060 ticket 含 auth/* 範圍，實作時撞牆改 defer，否則 baseline 直接停在 collection error。具體 reproduce + 兩條修法（cosmic-ray、conftest 改成 lazy / 不打 `app.main`）寫在 `api/pyproject.toml` `[tool.mutmut]` 註解。| T-054 dual-stack middleware 落地前評估；M3.5 期間若 auth 模組複雜度升高，優先 promote |
| S3-3 | Docker stack 與多 worktree 結構性錯位：`docker-compose.yml` 的 `./api/app:/app/app` 等 bind-mount 解析永遠指向主 repo（不論你 cwd 在哪 worktree），且整套 stack 全 worktree 共用一份 container；`docker cp` / `docker exec` 寫 `/app/...` 都會反向洩漏到主 repo 工作樹（2026-04-30 T-033 PR #47 開工時踩過）。`tickets/PARALLEL_WORKFLOW.md` §8 已寫 do/don't + T-031 「`docker run --rm -v $WORKTREE/api:/app`」正確 pattern，但這只是約定，沒結構性阻擋。三個可行修法：(a) 維持文件約定；(b) 改 per-worktree compose project name (`docker compose -p`)；(c) 殺掉 bind-mount source 改 image rebuild（破壞 hot-reload）。| M3.5 開工（OAuth provider docker container 進場時 docker stack 表面擴大）；或 Wave C+ 再有 worktree 踩到時 |

---

## 下一個 Session 開工前必讀

1. `CLAUDE.md` — 專案定位 + agent 切換
2. `DECISIONS.md` — 核心決策 quick ref
3. `tickets/T-XXX-*.md` — 本單完整內容
4. 單裡 **Planning refs** 列的檔案
