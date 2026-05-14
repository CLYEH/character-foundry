# T-068: SPA login page — Google direct shortcut + password fallback + dev entry

**Status:** TODO
**Sprint:** Backlog（post-3.5a UX follow-up；不 block M3.5 ship）
**Est:** S
**Depends on:** T-056（LoginPage + authStore dual-stack — 本單在 T-056 落地的 UI 上加分流）
**Related:** T-067（同樣是 3.5a ship 後排的 follow-up batch）

---

## Scope

SPA `/login` 頁面從「單按鈕 Google」改成三個入口：

1. **Google 登入** — 直跳 Google（透過 Authentik source-init URL，繞過 Authentik identification 中介頁）
2. **帳號密碼登入** — 走原本的 `/oauth/application/o/authorize/` flow → 落在 Authentik 內建 identification 頁，可輸入 username/password
3. **Dev →**（角落小按鈕）— 開 `/oauth/if/admin/` 新分頁，給 akadmin / 運維者進 Authentik admin UI

**In scope:**
- `web/src/lib/oauth-client.ts` 新增 `buildSourceInitUrl(authorizeUrl: string, sourceSlug: string)` helper — 回傳 `/oauth/source/oauth/login/<slug>/?next=<urlencoded authorize url>`
- `web/src/routes/login.tsx` 改成三按鈕版面：兩個主按鈕（Google / 帳密）+ 一個 dev escape hatch
- PKCE verifier / state stash 維持現狀，不變 — Google path 跟 password path 都重用同一份 `stashPkceState` + `buildAuthorizeUrl`，只是 Google path 在最外層再包一層 source-init
- 新增 env var `VITE_AUTHENTIK_GOOGLE_SOURCE_SLUG`（default `google`）— 對齊 `VITE_AUTHENTIK_CLIENT_ID` / `VITE_AUTHENTIK_AUTHORIZE_URL` 慣例，prod 可改 slug 或留空關掉 shortcut
- 修 `web/src/lib/oauth-client.test.ts` + `web/src/routes/login.test.tsx` 反映新 helper / 新按鈕
- 擴 `web/tests/e2e/oauth-login.spec.ts`：原本只測 password path（CI 沒 upstream Google source），新增一條斷言「Google button 跳轉目標 URL 含 `source/oauth/login/google/` segment」（不實際走 Google，因為 CI 無法 mock Google IdP）
- 修 `planning/frontend/oauth-integration.md` §1.1 / §1.3 —— 把「單按鈕 Google」反方案的決策改寫成「dual-path + dev escape hatch」，理由 see Notes

**Not in scope:**
- 在 Authentik identification 頁面把 Google 按鈕藏掉（要客製化 flow，工程量大；本單接受「使用者選了帳密入口進去仍看得到 Google 按鈕」這個 leakage，UX 影響低）
- 把 dev 按鈕做成 hover-only / production build 自動拿掉（先做最簡 footer link；之後若覺得擾民再做 prod build hide）
- 多 IdP picker（未來如果加 GitHub / Microsoft，要回頭把 Google button 變成 generic source picker）
- Logout flow 改動（T-056 revoke 已 land，本單不動）
- Mobile responsive 細調（兩個全寬按鈕 + 角落 dev link，預期不會 break；e2e 跑桌面尺寸）

---

## Planning refs（開工前必讀）

- `planning/frontend/oauth-integration.md` §1.1 + §1.3 — **要改的對象**：原本鎖「Sign in with Google 單一按鈕」+ 反方案 dual button，本單推翻
- `planning/devops/authentik-stack.md` §5.2 — Authentik OAuth Source `/source/oauth/login/<slug>/` URL 機制；callback path 帶 `/oauth/` 前綴的原因
- `web/src/lib/oauth-client.ts:83-98` — `buildAuthorizeUrl` 現狀，新 helper 在它外層包
- `web/src/routes/login.tsx:35-48` — `handleSignIn` 現狀，要拆成兩個 handler

---

## Acceptance criteria

- [ ] `/login` 頁面 render 三個元素：Google 主按鈕、帳密主按鈕、Dev → 小連結
- [ ] 點 Google → 瀏覽器 URL 變 `localhost/oauth/source/oauth/login/google/?next=...`（302 後到 `accounts.google.com`；CI 用 `expect(page.url()).toContain('source/oauth/login/google/')` 斷言中段 hop 而非實際到 Google）
- [ ] 點帳密 → 瀏覽器 URL 變 `localhost/oauth/application/o/authorize/?...`（原本 T-057 e2e 斷言維持）
- [ ] 點 Dev → → 新分頁開 `localhost/oauth/if/admin/`，原分頁停在 `/login`
- [ ] PKCE verifier 在 `sessionStorage` 兩條路徑下都正確 stash（key = `cf-oauth-pkce-verifier`），callback 端 `consumePkceState` 都能取回
- [ ] `pnpm test` 綠（unit + component）；`pnpm test:e2e` 綠（既有 + 新增 Google direct hop 斷言）
- [ ] `planning/frontend/oauth-integration.md` §1 改成新決策；保留舊版的理由（single-user 假設）並寫明為什麼推翻

---

## Files expected to touch

- `web/src/lib/oauth-client.ts` (edit) — 加 `buildSourceInitUrl`
- `web/src/lib/oauth-client.test.ts` (edit) — 新 helper 單測（URL 拼接、encode、empty slug 行為）
- `web/src/routes/login.tsx` (edit) — 三按鈕 layout + 兩個 handler
- `web/src/routes/login.test.tsx` (edit) — render 三按鈕 + click dispatch
- `web/src/config.ts` (edit) — 加 `authentik.googleSourceSlug` 讀 `VITE_AUTHENTIK_GOOGLE_SOURCE_SLUG`
- `web/tests/e2e/oauth-login.spec.ts` (edit) — 新增 Google direct hop 斷言；既有 password path 維持
- `.env.example` (edit) — 加 `VITE_AUTHENTIK_GOOGLE_SOURCE_SLUG=google`
- `planning/frontend/oauth-integration.md` (edit) — §1.1 / §1.3 改寫
- `DECISIONS.md` (edit, maybe) — 若這條 UX 決策夠 load-bearing 加進去快查；可以拒絕加，由 reviewer 判斷

---

## OAuth scope required

`n/a`（frontend ticket，沒新 endpoint）

---

## MCP tool delta

`n/a`（不動 MCP layer）

---

## Notes

### 為什麼推翻 `planning/frontend/oauth-integration.md` §1.1 / §1.3

原 §1.3 反方案的理由：

> Phase 1 internal users 都是你一個人，dual button 是 dead-weight UI

實際 dev / setup 過程 reveal 三條 reality：

1. **帳密 fallback 的真實需求**：本機 Google Workspace / 個人 Gmail OAuth client 在 Testing mode 下 refresh token 7d 過期；Workspace 帳號被 suspend / Google 自身停擺時 Google path 整條無法 recover。帳密入口是 break-glass。
2. **dev / admin 操作會回頭找 akadmin**：本次 setup 流程（建 Authentik、設 OAuth client、改 flow stage、加 Google Source）都得登 akadmin 進 admin UI。dev escape hatch 不藏起來，省下「我密碼存哪」的反覆查找。
3. **「單一按鈕簡潔」vs「多入口可救」trade-off 換邊**：單按鈕在「正常路徑」勝；多入口在「異常路徑」勝。Phase 1 還沒上 prod，但 setup / debug 比運轉時間長很多，dev 階段多入口贏。

未來上 prod / 對外開放時，帳密入口要不要保留再評估：若 user base 都用 Workspace SSO，dual button 又變 dead weight；若混合（外部協作者沒 Workspace），帳密入口仍有意義。**這個 trade-off 重新評估的時機是 M5 polish 或上 prod 前**，本單只負責改現狀。

### Source-init URL 機制細節

Authentik 的 `/source/oauth/login/<slug>/` view：

1. 收到 request 後檢查 query string 有沒有 `next` — 有就把目標 URL 寫進 session
2. 觸發該 source 的 OAuth flow（generate state → 302 to Google authorize endpoint）
3. Google 認證完 callback 到 `/source/oauth/callback/<slug>/`
4. Authentik match 或建 user，建 session
5. 如果 session 裡有 `next`，最後 302 到 `next` URL；否則進 default landing

SPA 把原本的 authorize URL（含 PKCE challenge / state / redirect_uri）整個丟進 `next`，所以 step 5 接回原本的 OAuth Auth Code + PKCE flow，跑 explicit consent，回 SPA `/auth/callback?code=...&state=...`。**PKCE / state 全程不變，security posture 等同 T-056**。

### 撞點預警

- **Authentik 2024.12 對 `next` 是否支援 absolute URL vs relative-only**：T-053 setup 沒驗過。若只吃 relative path，要把 `next` 從 `http://localhost/oauth/application/o/authorize/...` 改成 `/oauth/application/o/authorize/...`（去掉 origin），開工第一步先用 curl 試這條
- **`stashPkceState` 時機**：必須在跳 source-init **之前** stash 進 sessionStorage（同 origin），不能等回 SPA callback 才存 — 跟既有 `buildAuthorizeUrl` path 一樣的時機，handler 結構保留
- **Dev 按鈕 production build 不藏起來的代價**：對外開放後 random user 看到「Dev」會疑惑。Not-in-scope 但要記錄，M5 polish 評估要不要 prod build 條件 hide
- **E2E 無法走完整 Google 流程**：CI 環境沒 upstream Google source，新增斷言只到「跳轉 URL 對」這層；走真實 Google 留給手動 smoke

### 排程

3.5a ship gate (T-057) 已 DONE，這張屬於 ship 後 UX polish backlog。可以在 T-067（compose secret guard）之後或之前插，無 dep 衝突。
