# Frontend OAuth Integration（M3.5）

> **Owner:** Frontend Agent
> **Created:** 2026-05-07
> **Status:** Locked（M3.5 plan phase Step 4）
> **Upstream:** `planning/auth/open-questions.md` · `planning/agent-interface/open-questions.md` · `planning/backend/oauth-mcp-integration.md`

---

## 1. Login UI（三入口樣式 — T-068 改寫）

> 2026-05-14: 原本鎖「Sign in with Google 單一按鈕」（T-056 land），T-068 改成 Google direct + 帳密 fallback + Dev escape hatch。**舊版規則保留於 §1.4 作為決策歷史。**

### 1.1 規則

`/login` 三個入口：

1. **使用 Google 登入**（主按鈕）—— SPA 不直接呼 `/authorize`，而是把 authorize URL 包進 Authentik **source-init redirect**（`/oauth/source/oauth/login/<slug>/?next=<encoded authorize url>`），讓 Authentik 直接觸發指定 OAuth Source 的 flow，使用者跳過 identification 中介頁直達 Google。`<slug>` 由 `VITE_AUTHENTIK_GOOGLE_SOURCE_SLUG` 設定（default `google`，留空即關掉這顆按鈕）。
2. **使用帳號密碼登入**（fallback 按鈕）—— 走原本 `/oauth/application/o/authorize/` 進 Authentik 的 identification + password 兩 stage，break-glass / 非 Google 帳號用。
3. **Dev →**（角落小連結）—— 在新分頁開 `/oauth/if/admin/`，akadmin 或運維者進 Authentik admin UI。

PKCE verifier / state 由 `stashPkceState` 在跳轉前寫進 `sessionStorage`，Google 路徑跟帳密路徑共用，security posture 等同單按鈕版。

### 1.2 流程

**Google direct（主路徑）：**

```
[Login page]
   ↓ 按「使用 Google 登入」
[stashPkceState → window.location.assign(/oauth/source/oauth/login/google/?next=<authorize URL>)]
   ↓ Authentik 把 next 寫進 session，觸發 google source 的 OAuth flow
[redirect 到 Google login]
   ↓ Google 認證 → callback 回 /oauth/source/oauth/callback/google/
[Authentik match / create user + session]
   ↓ Authentik 從 session 取回 next，redirect 到該 authorize URL
[/oauth/application/o/authorize/?... → /auth/callback?code=...&state=...]
   ↓ AuthCallbackPage 用 code + PKCE verifier 換 token
[寫進 authStore tokenSource='oauth'，redirect 到 Dashboard]
```

**帳密 fallback：**

```
[Login page]
   ↓ 按「使用帳號密碼登入」
[stashPkceState → window.location.assign(/oauth/application/o/authorize/?...)]
   ↓ Authentik 顯示 identification stage（輸入 email/username）
   ↓ password stage（輸入密碼）
   ↓ 通過後 → /auth/callback?code=...&state=...
[換 token → authStore tokenSource='oauth' → Dashboard]
```

**Dev escape hatch：** 不走 OAuth；直接 new tab 開 `/oauth/if/admin/`。

### 1.3 為什麼需要三個入口（operator persona pass — T-068）

`planning/CLAUDE.md` §1 列的 operator persona pass 跑下來：

1. **Admin / config 路徑（Dev → 解決的是這條）**——改 OAuth client、Authentik flow、Google source 設定要進 admin UI。原本 `/oauth/if/admin/` 完全沒在 UI 上 surface，每次都要記 URL；做成 footer link 之後 setup / debug 入口 sticky。
2. **Break-glass 路徑（帳密 fallback 解決的是這條）**——Google Workspace OAuth client 在 Testing mode refresh 7d 過期；Workspace 帳號被 suspend、Google 自身停擺、或 source-init slug 改名 / 失效時，Google path 整條無法 recover。帳密入口走 Authentik 內建 identification page，獨立於 OAuth Source，是真正的 break-glass。
3. **主使用者路徑（Google direct 仍是主按鈕）**——日常登入仍最快：一鍵直達 Google，不經 identification 中介頁。

### 1.4 推翻舊規則的歷史紀錄

原 §1.1 鎖「**Sign in with Google」單一按鈕**：
- 理由 1：Phase 1 internal users 都是你一個人，dual button 是 dead-weight UI。
- 理由 2：舊 JWT login 在 dual-stack 過渡期還活著，UI 層只要新登入走 OAuth，舊 session 自然到期。
- 反方案 dual button（Google + email/password）標註為「multi-user 才有意義」。

**為什麼推翻：**

舊規則內部一致，但 plan time 把 user persona 跟 operator persona 折成同一人。實際 setup / debug 階段 reveal：

- Google OAuth client Testing mode refresh 7d 過期 → 沒帳密 fallback 整條無法 recover
- Setup / 改 Authentik 設定要 akadmin 進 admin UI → 完全沒在 SPA 上 surface
- 一旦 OAuth-only cutover 完成（T-056 已落地），連舊 JWT 登入頁也沒了，break-glass 完全消失

「單一按鈕簡潔」vs「多入口可救」trade-off 換邊：**單按鈕在「正常路徑」勝；多入口在「異常路徑」勝**。Phase 1 還沒上 prod，但 setup / debug 比運轉時間長很多，dev 階段多入口贏。

**未來 prod 上線時是否保留帳密入口需重新評估：** 若 user base 都用 Workspace SSO，dual button 又變 dead weight；若混合（外部協作者沒 Workspace），帳密入口仍有意義。重新評估的時機是 M5 polish 或上 prod 前。

### 1.5 Not in scope（T-068 範圍排除）

- Authentik identification 頁面上的 Google 按鈕沒藏掉（要客製化 flow，工程量大）；使用者選帳密入口進去仍會看到 Google 按鈕，UX 影響低
- Dev → 按鈕 production build 沒條件 hide（M5 polish 再評估）
- 多 IdP picker（未來如果加 GitHub / Microsoft，要把 Google button 變 generic source picker）

---

## 2. authStore（dual-stack 期間）

### 2.1 規則

統一一個 `authStore`，token 只暴露 `accessToken`，內部用 `tokenSource: 'jwt' | 'oauth'` discriminator。

### 2.2 Shape

```ts
type AuthState = {
  accessToken: string | null
  refreshToken: string | null
  tokenSource: 'jwt' | 'oauth' | null  // dual-stack 期間區分
  expiresAt: number | null
  user: User | null
}
```

### 2.3 API 呼叫不分情境

所有 fetch 一律 `Authorization: Bearer ${accessToken}`。後端 middleware 接受 JWT 或 OAuth token（per auth Q4 簡化 dual-stack），frontend 不必知道哪種。

### 2.4 Refresh logic

- `tokenSource === 'jwt'`：用既有 `/v1/auth/refresh` endpoint（dual-stack 期間還活著）
- `tokenSource === 'oauth'`：用 Authentik `/token` endpoint + refresh_token grant

封裝在 `authStore.refresh()` 一個 method，呼叫端不感知。

### 2.5 為什麼不開兩個 store

- 兩套 store 平行 = 每個 component 要決定「我該訂閱哪個」
- Phase 1 真正存在於系統中的 token 在任一時刻只有一種
- Migration 結束（JWT path 移除）後，`tokenSource` 欄位可刪、`refresh()` switch 可簡化

反方案 jwtStore + oauthStore 平行 —— complexity 翻倍，dual-stack 結束沒人記得清掉。

---

## 3. 改動範圍（指向 ticket，不在這定）

- `LoginPage.tsx` —— 移除 form，加「Sign in with Google」按鈕
- 新增 `AuthCallbackPage.tsx` —— 處理 OAuth code → token
- `authStore.ts` —— 加 `tokenSource` 欄位 + dual refresh path
- API client（fetcher）—— 不必改（仍是 `Bearer ${accessToken}`）

具體 ticket 在 Sprint 3.5a 開。
