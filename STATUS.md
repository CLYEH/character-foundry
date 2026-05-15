# Character Foundry — Implementation Status

> **Last updated:** 2026-05-15 — T-078 done：修 wall 6（已 provision 好的 operator 登出後無法 re-login）。**Root cause 跟 ticket 的三條候選都不完全對上** —— ticket 假設「SPA logout 也要結束 Authentik session」(a)、「放寬 `default-source-authentication`」(b)、「source-init 帶 `prompt=login`」(c)。實作走 (b)：把 `default-source-authentication.authentication` 從 Authentik 出廠的 `require_unauthenticated` 改成 `none`。理由是 (a) 的兩個變形（navigate 到 `default-invalidation-flow` / OIDC `end_session_endpoint`）CDP 實測都失敗 —— 前者的 `UserLogoutStage` 會 `auth_logout()` flush 整個 session 把 `SESSION_KEY_GET[next]` 一起 nuke，`_flow_done()` 沒 next 可 honor 回去 SPA，bounce 到 `default-authentication-flow` 卡住；後者的 `default-provider-invalidation-flow` 在這套 Authentik 出廠就沒 stage bindings、flow executor 永遠卡 `ak-loading`，換成有 `UserLogoutStage` 的 `default-invalidation-flow` 又遇 `_flow_done` 把 post-logout redirect 用相對路徑下發、browser 把它解析到 Authentik origin（dev 從 `:5173` flip 到 `:80`，UX 違反「logout 落回 SPA `/login`」）。(c) 結構上錯——block 點是 source-auth flow 的 `require_unauthenticated`、不是 upstream IdP，`prompt=login` 救不到。落地 (b)：SPA 端**零 code 改動**（純註釋）；codify 改在 `infra/authentik/blueprints/cf-e2e-bootstrap.yaml` upsert `default-source-authentication.authentication=none`（**只蓋 CI / e2e** —— 這支 blueprint 是 `docker-compose.test.yml` 掛起來的，dev `override.yml` 只掛 `cf-google-init.yaml`、prod 沒有對應 mount）；dev 已 `ak shell` 同步；prod setup 時必 admin-UI 或自己 codify 一條 prod-blueprint 補上（§5.9 checklist 已列）。CDP 在 real Chrome（leoyeh906）走 fresh login → logout → re-login 全 silent 通過。Side effect: `cf-e2e-bootstrap.yaml` 加 e2e regression test（雖然 e2e 走 password path 跑 `default-authentication-flow`，本來就 `auth=none`，但仍是 logout flow 的 smoke gate）。⚠ 本 codebase 只用一條 OAuth Source（Workspace Google）；未來若加第二條且該 source 需要「拒絕已 authenticated」的功能語意，clone 一條 source-specific auth flow，不要把這條再鎖回 `require_unauthenticated`。 — T-076 done（PR #101）：修 wall 4 —— dev `:5173` 下 Authentik flow interface 的 bootstrap XHR 跨來源（`:5173` → `:80`）被 CORS 擋、卡 `Loading…`。Root cause：flow interface 用 Authentik 絕對 `base_url`（`http://localhost/oauth/api/...`，因 nginx `$host` 去 port）發 XHR，跟 `:5173` SPA 跨來源。修法（候選 1 最小形式）：`VITE_AUTHENTIK_AUTHORIZE_URL` 改**絕對** → SPA 的 Google / 帳密兩個登入入口都直接導航到 Authentik 真實 origin `:80`，flow interface + 它的 XHR 同源、無 CORS；`redirect_uri` 仍 `:5173` 把人帶回 SPA；`TOKEN/LOGOUT_URL` 維持相對（`fetch` 同源）。**零前端 code 改動**（`buildAuthorizeUrl`/`buildSourceInitUrl`/帳密 path 都已能吃絕對 URL），只改 `.env.example` + 註解（`vite.config.ts`、`authentik-stack.md` §5.2.1a）。CI `pr.yml` 自己寫 `.env`、維持相對（CI 單源不受影響）。CDP run `r2` 驗證 fresh-session Google 登入 end-to-end 走到 Dashboard（`:5173/`、heading 我的角色）。**CDP 驗證連環 reveal 下游兩道牆，已開單**：wall 5 = T-077（operator 不在 `cf-agent-default` group → authorize endpoint 擋；§5.7 runbook 缺口；dev 已手動補）、wall 6 = T-078（logout 後 SPA 不結束 Authentik session → re-login 撞 `require_unauthenticated`；T-073 早預告、使用者實測確認；真功能 bug 優先級高）。⚠ 既有 dev `.env` 要手動把 `VITE_AUTHENTIK_AUTHORIZE_URL` 改絕對 + `docker compose up -d web`（`.env` 是 gitignored，本單只改 committed 的 `.env.example`）。 — T-075 done（PR #100）：修 T-073 ship 的 encoding regression。T-073 把 SPA URL 包成 `/oauth/if/flow/cf-google-init/?query=next=X`，但 flow **interface** 前端（`FlowInterface-2024.12.5.js`）會自己把 `window.location.search` bundle 進 executor API 的 `?query=` —— 多包一層 → executor `QueryDict` 出 `{query: "next=X"}` 沒有 `next` key → `_prepare_flow` 還是 fallback `/if/user/`。T-073 的 curl 驗證會過是因為它直接打 executor **API**（那層才要 `?query=`）；SPA 打的是 **interface**（吃 plain `?next=`、前端自己 bundle）。修法：`buildSourceInitUrl` 改產 plain `/oauth/if/flow/cf-google-init/?next=X`。CDP 驗證確認 encoding 修對（network log 看到正確的 executor 呼叫）。**但 CDP 同時 reveal wall 4** —— flow interface 的 bootstrap XHR 打 Authentik 絕對 `base_url`（`http://localhost/oauth/api/...`）跟 SPA origin `:5173` 跨來源 → CORS 擋 → 卡 `Loading…`；dev-`:5173`-only（prod/e2e 同源無此問題），拆 **T-076**。使用者拍板「先 ship T-075、再做 T-076」。⚠ 另一個踩過的坑：前幾輪 CDP 測試打到 stale pre-T-073 SPA code —— dev `web` 的 Vite file-watcher 沒抓到 Windows→Docker bind-mount 變更，要 `docker compose restart web` 讓它 cold-start 重掃。 — 2026-05-14 T-073 landed: 修 operator 首登 `next`-redirect 缺口（wall 3）。**Root cause 跟 ticket 假設不同** —— 不是 enrollment flow 設定問題，是 Authentik 2024.12.5 的 OAuth `OAuthRedirect` view（`sources/oauth/views/redirect.py`）**靜默忽略 `?next=`**：`SESSION_KEY_GET` 只由 flow-executor 的 `dispatch()` 寫入，bare source-init path 從不寫，所以 callback 回到 `_prepare_flow` 時 `final_redirect` fallback 到 `/if/user/`。影響 **enrollment + authentication 兩條 flow**（ticket 原假設「auth honor、enroll 不 honor」是錯的，兩條都走 `_prepare_flow`）。修法：新增 `cf-google-init` launcher flow（單一 RedirectStage → `/oauth/source/oauth/login/google/`），codify 在 `infra/authentik/blueprints/cf-google-init.yaml`；SPA `buildSourceInitUrl` 改導到 `/oauth/if/flow/cf-google-init/?query=next=...` 而非 bare source-init。blueprint dev 由 `docker-compose.override.yml` 單檔 mount、e2e 由 `docker-compose.test.yml` dir mount 共用。**Ticket 的「E2E gate N/A、不碰 SPA code」判定已修正** —— fix 確實需要 ~1 行 SPA 改動（`buildSourceInitUrl` 的 URL builder），e2e spec 同步更新成覆蓋 SPA→flow→source-init chain。驗證（automated）：blueprint apply 乾淨（`status=successful`）、flow executor 在 live session 寫入 `authentik/flows/get={next:<authorize URL>}`（= `_prepare_flow` 讀的那把 key）並回 `xak-flow-redirect` → source-init；完整 Google round-trip（AC #4）需真 Google 帳號，是 ticket 自己標記的「Manual」operator step。順手修 STATUS stale：T-066 之前標 TODO 但檔案已在 `tickets/DONE/`。 — T-070 landed: `web/vite.config.ts` dev proxy 補 `/oauth/` entry（target `http://nginx`、不 rewrite、`changeOrigin: false`）+ `/api` target 改 `http://api:8000`。`changeOrigin` 是這單的關鍵 deviation：ticket 原建議 `true`，CDP 驗證發現 `true` 會把 `Host` 改寫成 `nginx` → Authentik 用它拼出 `redirect_uri=http://nginx/...` → Google 直接 reject；`false` 保留瀏覽器真實 Host（nginx `$host` 去 port → `redirect_uri=http://localhost/...`，Google 收）。驗證走 CDP 連本機真實 Chrome 跑 end-to-end：`:5173/login` → vite proxy → nginx → Authentik source-init → Google（發出真 auth code）→ callback 回 Authentik，proxy hop 全通、無 bounce 回 `/login` —— proxy fix 本身完整驗證。CDP 測試另連環撞三道 operator-config wall（皆非 T-070 code scope）：wall 1 OAuth Source 沒設 enrollment flow（T-069 已文件化，本次 dev 用 `ak shell` 補上 `default-source-authentication` / `default-source-enrollment`）、wall 2 backend 無 `User` row（`provision-operator` CLI 補上 `leoyeh906@gmail.com`）、**wall 3 enrollment flow 完成不 redirect 回 SPA、落在 `/if/user/`，且 `default-source-authentication` 的 `require_unauthenticated` 讓重試撞 "Flow does not apply"** —— wall 3 是 T-069 runbook 沒涵蓋的真缺口。三道 wall 開了 T-071（backend OAuth auto-provisioning，落地 `authentik-stack.md` §5.7.2 留的 M3.5b deferred item）、T-072（nginx `/api/health` docker 內網 502，T-070 topology 驗證 reveal）、T-073（Authentik source enrollment `next`-redirect 缺口，operator-amendment）。 — 同日稍早 T-069 implemented：補上 T-053 §5.2 留的 dev operator provisioning 設定缺口（`authentik-stack.md` §5.2 補 OAuth Source 的 Authentication / Enrollment flow、新增 §5.7「Provision a dev operator」、`provision-operator` CLI 帶隨機不記錄 hash 把「OAuth-only」做進結構）。
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
| T-071 | Backend OAuth auto-provisioning — `_resolve_oauth` 第一次拿到有效 Authentik token 時自動建 backend `User` row（+ allowlist / `hd=` guardrail），取代手動 `provision-operator` CLI。落地 `authentik-stack.md` §5.7.2 留的 M3.5b deferred item。T-070 dev 測試 wall 2 reveal | TODO |
| T-072 | nginx `/api/health` docker 內網 502 — `http://nginx/api/health` 從 network 內回 502（e2e 走 nginx:80 真實路由綠，疑為 `/health` path-specific 小問題）。T-070 dev-proxy topology 驗證 reveal | TODO |
| T-073 | Authentik source enrollment `next`-redirect 缺口 — 真人 operator 首登走 enrollment flow 完成後落在 `/if/user/`、不 redirect 回 SPA，重試又撞 `require_unauthenticated` 的 "Flow does not apply"。operator-amendment（補 `authentik-stack.md` §5.2）。T-070 dev 測試 wall 3 reveal | DONE |
| T-074 | Authentik flow-executor `next` open-redirect — `?query=next=https://evil.com` 在登入完成後會被 redirect 出站；Authentik core `_flow_done` 的 `PLAN_CONTEXT_REDIRECT` path 不驗證 `next`。既有行為、每條 flow-executor URL 都有，非 T-073 引入。修法：綁 expression policy 驗證 same-origin。T-073 security review defer | TODO |
| T-075 | T-073 regression — `buildSourceInitUrl` 把 `next` 包成 `?query=next=` 多包一層；flow interface 前端會自己把 `location.search` bundle 進 executor 的 `?query=`，結果 executor 拿到 `{query: "next=X"}` 沒有 `next` key → `_prepare_flow` 還是 fallback `/if/user/`。修法：改產 plain `?next=`。T-073 AC#4 CDP 測試抓到 | DONE |
| T-076 | flow interface XHR CORS（dev `:5173`）— `cf-google-init` flow interface 載得起來、executor 呼叫格式也對（T-075 已修），但 interface 的 bootstrap XHR 打 Authentik 絕對 `base_url`（`http://localhost/oauth/api/...`）跟 SPA origin `:5173` 跨來源 → CORS 擋 → 卡 `Loading…`。dev-only（prod/e2e 同源）。修法：`VITE_AUTHENTIK_AUTHORIZE_URL` 改絕對 → 導航直接到 `:80` 真實 origin。CDP 驗證 fresh-session Google 登入 end-to-end 到 Dashboard。T-075 CDP 驗證 reveal（wall 4）| DONE |
| T-077 | operator group provisioning 缺口（wall 5）— `Character Foundry SPA` application policy-bind `cf-agent-default` group，但 `authentik-stack.md` §5.7 的 operator-provisioning runbook 沒把 operator 加進去 → 新 operator 過了 Authentik 登入卻被 authorize endpoint "Permission denied"。dev 已手動補；runbook / CLI 缺口待修。T-076 CDP 驗證 reveal | TODO |
| T-078 | logout 後無法 re-login（wall 6）— SPA logout 只 revoke OAuth token、不結束 Authentik session → re-login 時 `default-source-authentication` 的 `require_unauthenticated` 判 "Flow does not apply" → 拒。T-073 早預告、T-076 後使用者實測確認。真功能 bug（連已 provision 的 operator 都中），優先級高於 T-077。| DONE |

#### Sprint 3.5b / 3.5c — 未開單（3.5a ship 完再開）

**Plan phase deliverable：**
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
