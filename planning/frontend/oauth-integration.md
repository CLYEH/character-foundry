# Frontend OAuth Integration（M3.5）

> **Owner:** Frontend Agent
> **Created:** 2026-05-07
> **Status:** Locked（M3.5 plan phase Step 4）
> **Upstream:** `planning/auth/open-questions.md` · `planning/agent-interface/open-questions.md` · `planning/backend/oauth-mcp-integration.md`

---

## 1. Login UI（cutover 樣式）

### 1.1 規則

**「Sign in with Google」單一按鈕**。Phase 1 upstream IdP 是公司 Google Workspace，使用者按下按鈕後整段 Auth Code + PKCE 在 Authentik 跑完。

不保留 email/password 表單。

### 1.2 流程

```
[Login page]
   ↓ 按「Sign in with Google」
[redirect 到 Authentik /authorize]
   ↓ Authentik 偵測 upstream IdP
[redirect 到 Google login]
   ↓ 你用公司 Google Workspace 帳號登入
[Google 回 Authentik]
   ↓ Authentik 簽發 OAuth code
[callback 回 frontend /auth/callback?code=...]
   ↓ frontend 用 code + PKCE verifier 換 token
[POST Authentik /token]
   ↓ 拿 access + refresh token
[寫進 authStore，redirect 到 Dashboard]
```

### 1.3 為什麼不留 email/password

- Phase 1 internal users 都是你一個人，dual button 是 dead-weight UI
- 既有 JWT login 在 dual-stack 過渡期照常運作（後端兼容），UI 層只要新登入走 OAuth，舊 session 自然到期就好（auth Q7 軟切換）

反方案 dual button（Google + email/password）—— multi-user 才有意義。

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
