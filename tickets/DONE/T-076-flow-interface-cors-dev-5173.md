# T-076: Authentik flow interface XHR is CORS-blocked on the `:5173` dev origin — flow stuck at "Loading…"

**Status:** DONE
**Sprint:** Backlog（post-3.5a；dev-topology wall — T-075 CDP 驗證 reveal，同 T-070 family）
**Est:** S
**Depends on:** T-075（encoding fix 先 land；本單疊在它之上）
**Related:** T-073（引入「走 flow interface」這條路）、T-070（dev proxy topology，本單是它的延伸）、T-075（encoding fix）、T-077（同 CDP 驗證 reveal 的 wall 5）、T-078（使用者實測 reveal 的 wall 6）

---

## Scope

修「SPA 走 `cf-google-init` flow 時，Authentik flow **interface** 的 bootstrap XHR 在 `:5173` dev origin 被 CORS 擋掉、整頁卡在 `Loading…`」。

### 怎麼發現的（2026-05-15，T-075 CDP 驗證）

T-075 把 `buildSourceInitUrl` 的 encoding 修對後（network log 確認 SPA 已導到正確的 `/oauth/if/flow/cf-google-init/?next=<authorize URL>`、flow interface 也發出格式正確的 `flows/executor/cf-google-init/?query=next%3D%252Foauth...` 呼叫），flow interface 仍卡在 `Loading…`。CDP console log：

```
Access to fetch at 'http://localhost/oauth/api/v3/core/brands/current/'
  from origin 'http://localhost:5173' has been blocked by CORS policy:
  Response to preflight request doesn't pass access control check
Access to fetch at 'http://localhost/oauth/api/v3/root/config/' ... blocked by CORS
Access to fetch at 'http://localhost/oauth/api/v3/flows/executor/cf-google-init/?query=...' ... blocked by CORS
```

### Root cause

- SPA 在 `http://localhost:5173`。點「使用 Google 登入」→ 導到 `http://localhost:5173/oauth/if/flow/cf-google-init/?next=...`（vite proxy `/oauth/` → nginx → Authentik，**flow interface HTML 載得起來**）。
- 但 flow interface 的前端用 Authentik 注入的 `base_url` 發 API XHR。`base_url` 由 `core/views/interface.py` 的 `self.request.build_absolute_uri(...)` 算出來 —— 而 T-070 讓 nginx 用 `$host`（去 port）→ Authentik 看到 `Host: localhost`（無 `:5173`）→ `base_url = http://localhost/oauth/`。
- 所以 flow interface 的 XHR 打 **絕對 URL** `http://localhost/oauth/api/v3/...`（port 80），跟它所在的 origin `http://localhost:5173` 跨來源 → preflight → Authentik 不回 `Access-Control-Allow-Origin: http://localhost:5173` → 全部被擋 → interface 卡在 `Loading…`、RedirectStage 永遠沒機會跑。

**這是 dev-`:5173`-only。** Prod / CI e2e 整套同 origin（`nginx:80`），flow interface + API 同源、無 CORS —— 所以 e2e 綠、prod 沒事。是 T-073「走 flow interface」這條路撞上 T-070 dev-proxy topology 的後果。

**In scope:**
1. 讓 dev `:5173` 下 flow interface 的 bootstrap XHR（`core/brands/current`、`root/config`、`flows/executor/...`）不被 CORS 擋。
2. 不破壞 T-070 已修好的東西（Google `redirect_uri` 要維持 `http://localhost/...`、proxy hop 維持通）。
3. 不破壞 prod / CI e2e 的同源路徑。

**Not in scope:**
- T-075 的 encoding fix（已 land / 進行中）。
- T-074 的 open-redirect hardening。

---

## Planning refs（開工前必讀）

- `planning/devops/authentik-stack.md` §5.2.1 — `cf-google-init` flow 機制
- `web/vite.config.ts` + T-070 ticket（`tickets/DONE/T-070-*.md`）— dev proxy topology、`changeOrigin: false`、nginx `$host` 去 port 的決策脈絡
- memory `reference_authentik_web_path_nginx_routing` — nginx `/oauth/` 反代規則
- memory `feedback_verify_oauth_flow_via_cdp_before_ship` — 為什麼這類問題只有 CDP 真瀏覽器測得到

---

## 候選修法（plan 時收斂）

1. **SPA 直接導到 `:80` origin 的 flow interface** —— `buildSourceInitUrl` 在 dev 產**絕對** `http://localhost/oauth/if/flow/cf-google-init/?next=...`。flow interface 從 `:80` 載 → 它的 XHR 打 `http://localhost/oauth/api/...` 同源、無 CORS。最後 `redirect_uri` 仍是 `http://localhost:5173/auth/callback` 把人帶回 SPA。需要 SPA 知道 Authentik 的 `:80` origin（新 env var 或讓 `authorizeUrl` 在 dev 設絕對）。
2. **nginx / Authentik 加 CORS headers** —— dev-only 對 `/oauth/api/` 回 `Access-Control-Allow-Origin: http://localhost:5173` + `Allow-Credentials: true`。較侷限但改動面小。
3. **讓 Authentik emit 相對 `base_url`** —— 若 Authentik 有設定能讓 `base_url` 相對化（`/oauth/` 而非 `http://localhost/oauth/`），XHR 就會是相對路徑、走 vite proxy 同源。需查 Authentik 2024.12.5 有沒有這個開關。

plan 時三條都評估；偏好不破壞 T-070、不增 prod 表面積的那條。

---

## Acceptance criteria

- [x] CDP re-verify：fresh session → `:5173/login` → 「使用 Google 登入」→ flow interface **不卡 `Loading…`**、RedirectStage 有跑 → Google → callback → **落在 SPA Dashboard** —— CDP run `r2` 實測：全程在 `:80` 跑，executor call 回 200，Google → callback → `default-source-authentication` → authorize → token → `http://localhost:5173/`，heading「我的角色」
- [x] CDP console 無 CORS error —— `r2` console 乾淨；對照 `r1`（修法前）滿是 `blocked by CORS policy`
- [x] T-070 的 Google `redirect_uri` 行為不變、proxy hop 仍通 —— 沒碰 nginx / vite proxy `changeOrigin`；導航改走 `:80` 絕對 URL，`redirect_uri` 仍由 `window.location.origin`（`:5173`）derive
- [ ] CI e2e（同源路徑）維持綠 —— PR auto-loop 驗（CI `pr.yml` 自己寫 `.env`、維持相對 `authorizeUrl`，單源不受影響）
- [x] 對應 planning doc 更新 —— `authentik-stack.md` 新增 §5.2.1a、`.env.example` + `vite.config.ts` 註解

---

## Resolution（2026-05-15）

**採候選修法 1 的最小形式：`VITE_AUTHENTIK_AUTHORIZE_URL` 改絕對**（dev = `http://localhost/oauth/application/o/authorize/`）。

評估三條候選後：候選 2（nginx CORS headers）會在共用的 `nginx.conf` 製造 prod/dev 分歧；候選 3（相對 `base_url`）不可行 —— Django `build_absolute_uri` 永遠回絕對。候選 1 最乾淨：authorize URL 絕對 → SPA 的「使用 Google 登入」與「帳密」兩個入口都直接導航到 Authentik 真實 origin `:80` → flow interface + 它的 bootstrap XHR 同源、無 CORS。`redirect_uri` 仍是 `:5173`（由 `window.location.origin` derive），登完回 SPA。`TOKEN_URL` / `LOGOUT_URL` 維持相對（SPA 從 `:5173` 發的 `fetch`，同源 + vite proxy 正確）。**零前端 code 改動** —— `buildAuthorizeUrl` / `buildSourceInitUrl` / 帳密 path 都已能吃絕對 URL。

落地：`.env.example` 的 `VITE_AUTHENTIK_AUTHORIZE_URL` 改絕對 + 詳細註解；`vite.config.ts` `/oauth` proxy 註解、`authentik-stack.md` §5.2.1a 同步。CI `pr.yml` 自己寫的 `.env` 維持相對（CI 單源、相對也同源 —— 不必動，動了反而有絕對-URL 對不上 CI host 的風險；`pr.yml` 加了交叉引用註解說明 divergence 是刻意的）。

**Trust boundary 有重新確認過：** `buildSourceInitUrl` docstring 標 `authorizeUrl` 會成為 post-callback redirect target、且「effectively NO backstop」。改絕對後 `next` 從相對路徑變成 fully-qualified `http://localhost/...`，理論上「`next` 能表達的東西」變寬 —— 但仍安全，因為 `authorizeUrl` 永遠是 config-derived（`buildAuthorizeUrl` 的輸出），不是 user input。那條 invariant（config-derived、never user input）沒變、仍 load-bearing。

**CDP 驗證連環 reveal 下游兩道牆（皆非 T-076 scope，已開單）：**
- **wall 5（T-077）** —— operator `leoyeh906` 不在 `cf-agent-default` group → `Character Foundry SPA` application 的 policy binding 在 authorize endpoint 擋下。dev 已手動把 operator 加進 group；runbook 缺口開 T-077。
- **wall 6（T-078）** —— 使用者實測：logout 後無法 re-login。SPA logout 不結束 Authentik session → re-login 撞 `default-source-authentication` 的 `require_unauthenticated`。T-073 早預告、本次確認，開 T-078。

T-076 自身 scope（flow interface CORS）已驗證完成 —— fresh-session Google 登入 end-to-end 通。

---

## Files expected to touch

- `web/src/lib/oauth-client.ts` 或 `web/vite.config.ts` 或 `infra/nginx/*`（視收斂的修法）
- `web/.env.example` / `.env`（若加 env var）
- `planning/devops/authentik-stack.md` §5.2.1（edit）
- `STATUS.md` (edit)

> **E2E coverage gate（CONTRIBUTING §3.5）：** 視修法而定 —— 若改 SPA URL builder 行為，更新 `oauth-login.spec.ts`；若純 nginx / env 改動則可能 N/A。實作者於 PR 說明。dev-`:5173` 真瀏覽器路徑結構上 CI 覆蓋不到（同 T-070），靠 CDP 手動驗。

---

## OAuth scope required（後端 endpoint 必填；frontend / docs / infra 票寫 `n/a`）

`n/a`

---

## MCP tool delta（agent surface 影響；無影響寫 `n/a`）

`n/a`

---

## Notes

### 為什麼 T-073 / T-075 的 CI + e2e 沒抓到

CI e2e 整套跑在 `nginx:80` 同 origin —— flow interface 跟 Authentik API 同源、沒有 CORS preflight。`:5173` dev origin 是 vite proxy 拼出來的「假同源」，flow interface 的絕對-URL XHR 戳破了這個假象。典型「CI 看不到的 dev-only topology 缺口」，跟 T-070 同類。只有 CDP 連真瀏覽器走 `:5173` 才測得到。

### 牆的順序（operator 首登 dev 路徑）

T-068 reveal → wall 1（source flow 沒設，T-069）、wall 2（backend User row，T-071）、wall 3（`next` 不 redirect，T-073）；T-070 dev proxy；T-073 的 `?query=` encoding bug（T-075）；**wall 4 = 本單（flow interface XHR CORS）**。
