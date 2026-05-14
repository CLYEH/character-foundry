# T-070: Vite dev server `/oauth/` proxy — OAuth login broken at `localhost:5173`

**Status:** TODO
**Sprint:** Backlog（post-3.5a；dev 流程 reveal —— 同 T-068/T-069 的 operator-persona family）
**Est:** S
**Depends on:** none
**Related:** T-056（OAuth login flow 落地）、T-068（Google source-init shortcut —— 就是踩這個 relative `/oauth/` URL）、T-069（operator provisioning —— 另一道 stacked blocker，見 Notes）

---

## Scope

讓 OAuth 登入流程在 **SPA 從 `http://localhost:5173` 開啟時**（dev 的主要入口）能正常運作。現況：`web/vite.config.ts` 的 dev proxy **只 proxy `/api`，沒有 `/oauth/`**，而且連 `/api` 的 target 也是錯的（`http://localhost:8000` 在 containerized `pnpm dev` 裡指不到 api container）。結果在 `:5173` 點「使用 Google 登入」或「使用帳號密碼登入」都登不進去。

**In scope:**

1. **`web/vite.config.ts` 加 `/oauth/` proxy entry** —— SPA 的 OAuth URL 是 relative path（`VITE_AUTHENTIK_AUTHORIZE_URL=/oauth/application/o/authorize/`，刻意 relative 以 mirror prod nginx）。`window.location.assign('/oauth/source/oauth/login/google/?next=...')` 在 `:5173` origin 解析後打到 Vite dev server，而 Vite 沒有 `/oauth/` proxy → 回 SPA shell（`index.html`）→ SPA router 不認得 `/oauth/...` → 把人 bounce 回 `/login?redirect_back=...`。要加 `/oauth/` proxy 讓這條 navigation 真的到 Authentik。
2. **修 `/api` proxy 的 target** —— 現在是 `http://localhost:8000`。`web` service 跑的是 **containerized `pnpm dev`**（docker-compose override，`character-foundry_default` network），container 裡的 `localhost:8000` 是 container 自己，不是 api container。實測 `curl http://localhost:5173/api/health` → HTTP 500。
3. **dev-proxy topology 決定** —— proxy target 要用 docker service name（`web` 跑在 docker network 內），不是 `localhost`。實測（從 `web` container 內 `node fetch`）：
   - `http://nginx/oauth/application/o/authorize/` → 404（nginx 有把 `/oauth/` 轉到 Authentik，paramless authorize 回 404 = 有打到 Authentik）✅ → `/oauth/` proxy 建議 target `http://nginx`，不 rewrite（保留 `/oauth/` 前綴，見 memory `reference_authentik_web_path_nginx_routing`）
   - `http://api:8000/health` → 200 ✅ → `/api` proxy 維持現有 rewrite（`/api` → ``），target 改成 `http://api:8000`
   - `http://nginx/api/health` → 502（nginx 的 `/api` upstream 從 network 內打回 502 —— 見 Notes，本單不處理）

**Not in scope**（保留給其他單 / 其他流程）：

- **T-069 的 Authentik admin-UI 設定（OAuth Source flows）** —— 這是**第二道 stacked blocker**：就算本單修好 proxy，登入仍會在 Authentik 端撞 T-069 wall 1（`Source is not configured for enrollment`），因為 OAuth Source 的 Authentication / Enrollment flow 還沒在 admin UI 設。那是 operator setup 動作不是 code bug，文件在 `planning/devops/authentik-stack.md` §5.2 / §5.7（T-069 已補）。本單只負責「proxy hop 通」。
- **把 `VITE_AUTHENTIK_*` 改成 absolute URL** —— relative 是刻意設計（mirror prod nginx 同源）。修法是讓 dev proxy 正確 mirror nginx，不是改 URL 形態。改 absolute 還會跟 Authentik 註冊的 `redirect_uri=http://localhost:5173/auth/callback` 打架。
- **nginx `/api` upstream 回 502 的問題** —— 從 network 內打 `http://nginx/api/health` 回 502。e2e 走 nginx:80 且綠，代表 nginx 的 `/api` 對真實路由是通的，`/health` 這條可能是 path-specific 小問題。與本單（dev proxy）正交，要查另開單。
- **prod / nginx 設定** —— nginx 已正確路由 `/oauth/` 與 `/api/`，不動。

---

## Planning refs（開工前必讀）

- `planning/frontend/oauth-integration.md` —— SPA 的 OAuth login flow 規格（relative-URL 設計、source-init redirect、PKCE handoff）
- `planning/devops/authentik-stack.md` §5.2 / §5.7 —— stacked blocker（wall 1）的脈絡，說明為什麼修好 proxy 後還不能 end-to-end 登入
- `web/vite.config.ts` —— 要改的檔；現有 `/api` proxy 的 comment「Mirror prod nginx」就是這次要補完的 mirror
- memory `reference_authentik_web_path_nginx_routing` —— `/oauth/` 前綴不可 strip

---

## Acceptance criteria

- [ ] `web/vite.config.ts` 有 `/oauth/` proxy entry；`http://localhost:5173/oauth/application/o/authorize/?...` 會打到 Authentik（不是回 SPA shell `index.html`）
- [ ] `/api` proxy target 修好；`http://localhost:5173/api/health` 回後端 health response（不是 500）
- [ ] 從瀏覽器在 `localhost:5173` 點「使用 Google 登入」，瀏覽器真的 navigate 到 Authentik 的 Google source-init（URL 含 `/oauth/source/oauth/login/google/`），不是 bounce 回 `/login?redirect_back=`
- [ ] `pnpm e2e` 仍綠（e2e 走 nginx:80，本改動不可造成 regression）
- [ ] Manual：在 `:5173` 跑一次 OAuth 登入。若 T-069 的 Authentik flows 尚未設好，至少驗到「proxy hop 通、到 Authentik」並在 PR 註明卡在 wall 1（T-069）

---

## Files expected to touch

- `web/vite.config.ts` (edit) —— 加 `/oauth/` proxy、修 `/api` target 為 docker service name
- `STATUS.md` (edit) —— 完成時更新

> **E2E coverage gate（CONTRIBUTING §3.5）：** 預期 **N/A** —— 改的是 Vite dev server proxy 設定，不是 React Router route / critical action 的 code。既有 e2e（走 nginx:80）已覆蓋 OAuth flow 且必須維持綠。dev-only 的 `:5173` origin 難用現有 e2e harness 覆蓋（harness 走 nginx）。實作者若不同意此判定，於 PR 說明。

---

## OAuth scope required（後端 endpoint 必填；frontend / docs / infra 票寫 `n/a`）

`n/a`（不新增 / 改動 endpoint；純前端 dev tooling 設定）

---

## MCP tool delta（agent surface 影響；無影響寫 `n/a`）

`n/a`

---

## Notes

### 怎麼發現的（2026-05-14）

使用者要求測 CF 的 Google 登入。透過 CDP 連到使用者本機真實 Chrome（帶其 Google session），開 `http://localhost:5173/login`，點「使用 Google 登入」。觀察：頁面沒有任何可見變化，URL 變成
`http://localhost:5173/login?redirect_back=%2Foauth%2Fsource%2Foauth%2Flogin%2Fgoogle%2F%3Fnext%3D...` —— 人被 bounce 回登入頁。

### 機制（root cause）

1. `login.tsx` → `buildSourceInitUrl()`（`web/src/lib/oauth-client.ts`）產出 **relative path** `/oauth/source/oauth/login/google/?next=<encoded authorize url>`。relative 是因為 `authentik.authorizeUrl` 來自 `VITE_AUTHENTIK_AUTHORIZE_URL`，`.env` / `.env.example` 都設成 relative `/oauth/application/o/authorize/`（刻意，mirror prod nginx 同源）。
2. `window.location.assign('/oauth/source/...')` 對 `http://localhost:5173` origin 解析 → 瀏覽器 navigate 到 `http://localhost:5173/oauth/source/oauth/login/google/?next=...`。
3. `web/vite.config.ts` 的 proxy block **只有 `/api`，沒有 `/oauth/`** → Vite dev server 對 `/oauth/*` 走 SPA fallback 回 `index.html`。實測 `curl http://localhost:5173/oauth/application/o/authorize/` → `HTTP 200 text/html 564b`（= SPA shell）。
4. SPA React Router 不認得 `/oauth/source/oauth/login/google/` → auth guard / catch-all 把人 redirect 到 `/login?redirect_back=<該 path>`。`login.tsx::safeRedirectBack` 的 `isSafeInternalPath` 接受它（以 `/` 開頭、非 `//`），所以 SPA 以為使用者「想去」那個 internal path。

帳密 fallback 按鈕同樣壞（`buildAuthorizeUrl` 也是 relative `/oauth/...`）。token exchange / revoke（`tokenUrl` / `logoutUrl`）也都是 relative `/oauth/...`，同樣會壞。

### 為什麼 CI 沒抓到

e2e（`web/tests/e2e/*.spec.ts`）跑在 docker-compose 整套 stack，透過 **nginx:80** 存取 —— 那個 origin 下 nginx 同時 serve SPA + proxy `/oauth/` + proxy `/api/`，relative path 都同源解析得到。只有「人在本機 `pnpm dev` 開 `:5173`」這條 dev path 會踩到。典型 operator-persona gap（`planning/CLAUDE.md` §1），同 T-068 / T-069 family。

### 建議修法（已驗證 target 可達）

從 `web` container 內實測（`docker compose exec web node -e "fetch(...)"`）：

| target | 結果 | 用途 |
|---|---|---|
| `http://nginx/oauth/application/o/authorize/` | 404（= 有打到 Authentik） | `/oauth/` proxy target，**不 rewrite**（保留前綴） |
| `http://api:8000/health` | 200 | `/api` proxy target（維持現有 `/api` → `` rewrite） |
| `http://nginx/api/health` | 502 | 不用這條當 `/api` target；502 本身另開單查 |

`vite.config.ts` proxy 大致長這樣（實作者自行確認）：

```js
proxy: {
  '/api': {
    target: 'http://api:8000',   // was http://localhost:8000 — unreachable in-container
    changeOrigin: true,
    rewrite: (p) => p.replace(/^\/api/, ''),
  },
  '/oauth': {
    target: 'http://nginx',      // nginx routes /oauth/ -> Authentik, prefix preserved
    changeOrigin: true,
  },
},
```

⚠ topology 注意：service name target（`api` / `nginx`）只在「`pnpm dev` 跑在 docker `web` container 內」時可解析。docker-compose override 確實是這樣跑（`./web:/app` mount + `pnpm dev`）。若未來要支援「host 直接跑 `pnpm dev`」，target 需另想（host 上 api / nginx 沒對應的可解析名稱，且 api container 沒 publish 到 host）。本單先服務現行 containerized 設定。

### Stacked blocker —— 修好本單還不夠

就算 proxy 修好，從 `:5173` 點 Google 登入會 navigate 到 Authentik，然後**撞 T-069 的 wall 1**：Authentik `google` OAuth Source 的 Authentication / Enrollment flow 還沒在 admin UI 設（`authentik_core_source` 的 `google` row `authentication_flow_id` / `enrollment_flow_id` 都 NULL）。那是 operator setup 動作（`planning/devops/authentik-stack.md` §5.2 / §5.7 已寫步驟），不是本單 code scope。本單 acceptance 只要求驗到「proxy hop 通、navigate 到 Authentik」。要 end-to-end 登入成功，需先做完 T-069 文件裡那段 admin-UI 設定。
