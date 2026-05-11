# T-056: Frontend Sign in with Google + AuthCallbackPage + authStore dual-stack

**Status:** TODO
**Sprint:** 3.5a
**Est:** M
**Depends on:** T-054（後端要能接 OAuth Bearer token）、T-053（Authentik client `character-foundry-spa` 必須存在）
**Related:** T-057（E2E smoke 跑本單實作的流程）

---

## Scope

把 frontend login 從現有 email/password 表單換成「Sign in with Google」單按鈕，串完 Authorization Code + PKCE flow，並改 `authStore` 支援 dual-stack（per frontend F1 + F2）。

**In scope:**

### LoginPage
- 移除 email/password form
- 加「Sign in with Google」按鈕，按下後：
  - 產生 PKCE `code_verifier` + `code_challenge`（用 `crypto.subtle.digest`）
  - 把 `code_verifier` 暫存 sessionStorage
  - Redirect 到 `${AUTHENTIK_AUTHORIZE_URL}?response_type=code&client_id=character-foundry-spa&redirect_uri=${origin}/auth/callback&code_challenge=${challenge}&code_challenge_method=S256&scope=character:read+character:write+task:read+task:cancel+usage:read`

### AuthCallbackPage（新 route `/auth/callback`）
- 從 URL query 取 `code` + `state`（驗 state 防 CSRF）
- 從 sessionStorage 取 `code_verifier`
- `POST ${AUTHENTIK_TOKEN_URL}` 換 access + refresh token
- 寫進 `authStore`（`tokenSource: 'oauth'`）
- Redirect 到 `/dashboard`（或登入前的目標 path）
- 失敗：顯示錯誤 + 「重試」按鈕

### authStore
- 加 `tokenSource: 'jwt' | 'oauth' | null`
- `refresh()` method 根據 `tokenSource` 選 path：
  - `'jwt'` → `POST /v1/auth/refresh`（既有 JWT path，dual-stack 期間）
  - `'oauth'` → `POST ${AUTHENTIK_TOKEN_URL}` with `grant_type=refresh_token`
- `setAuth({ accessToken, refreshToken, tokenSource, expiresAt, user })` 統一入口
- `logout()`：OAuth 額外打 `${AUTHENTIK_LOGOUT_URL}` revoke token

### Fetcher 不改
- 仍然 `Authorization: Bearer ${accessToken}`（後端 T-054 兩種都接）

### Tests
- `authStore.test.ts`：dual-stack refresh path、logout、setAuth round-trip
- `LoginPage.test.tsx`：PKCE 產生正確 challenge、按鈕觸發正確 redirect URL
- `AuthCallbackPage.test.tsx`：成功 / state 不對 / token exchange 失敗 三 case

**Not in scope:**
- 既有 JWT 路徑刪除（dual-stack 結束的 cleanup ticket）
- Logout 觸發 Authentik global session 結束（Phase 1 single user 沒需求；只 revoke token）
- E2E test（T-057）

---

## Planning refs

- `planning/frontend/oauth-integration.md` §1（Login UI）+ §2（authStore）+ §3（改動範圍）
- `planning/auth/open-questions.md` Q7（軟切換）
- `planning/agent-interface/open-questions.md` Q5 sub-5a（scope 清單對齊 backend）

---

## Acceptance criteria

- [ ] LoginPage 只剩「Sign in with Google」按鈕，無 email/password form
- [ ] 按下按鈕後跳到 Authentik authorize URL（內含 PKCE challenge + state）
- [ ] Google 登入成功後 callback 拿到 token，redirect 到 `/dashboard`
- [ ] authStore `tokenSource` 在 OAuth 登入後是 `'oauth'`，既有 JWT session 仍是 `'jwt'`（dual-stack 並存可工作）
- [ ] Token 過期時 `refresh()` 走對的 endpoint（JWT path 對 `/v1/auth/refresh`、OAuth path 對 Authentik token endpoint）
- [ ] Logout 在 OAuth 模式下打 Authentik revoke endpoint（用 network spy 驗證）
- [ ] `npm run test` 全綠（含新 spec）
- [ ] 手動測試：JWT 登入的舊瀏覽器 session 在本單 ship 後仍可正常呼 API

---

## Files expected to touch

- `web/src/pages/LoginPage.tsx` (edit) — 移除 form + 加 Google button
- `web/src/pages/AuthCallbackPage.tsx` (new) — `/auth/callback` route 處理
- `web/src/stores/authStore.ts` (edit) — 加 `tokenSource` + dual refresh
- `web/src/lib/oauth-client.ts` (new) — PKCE helpers + token exchange
- `web/src/App.tsx`（or router config）(edit) — 加 `/auth/callback` route
- `web/src/config.ts` (edit) — Authentik URL env
- `web/tests/unit/authStore.test.ts` (edit)
- `web/tests/unit/LoginPage.test.tsx` (edit)
- `web/tests/unit/AuthCallbackPage.test.tsx` (new)
- `.env.example` / `web/.env.example` (edit) — `VITE_AUTHENTIK_*` envs
- `tickets/T-056-frontend-oauth-login-ui.md` (new — 本單)
- `STATUS.md` (edit)

---

## OAuth scope required

`n/a`（frontend ticket，不開 backend endpoint）

---

## MCP tool delta

`n/a`

---

## Notes

- **PKCE 實作**：用 Web Crypto `crypto.subtle.digest('SHA-256', ...)` 產 `code_challenge`，base64url encode（去掉 `+/=`）。避免靠第三方 lib，PKCE 邏輯 < 20 行
- **State CSRF 防護**：sessionStorage 存 random state，callback 比對；state 不對直接 reject
- **sessionStorage vs localStorage**：`code_verifier` + state 走 sessionStorage（單 tab 生命週期）；access/refresh token 走 authStore（zustand persist localStorage，per 既有規範）
- **Authentik logout endpoint**：`/application/o/<app-slug>/end-session/` 加 `id_token_hint=...&post_logout_redirect_uri=...`。需要儲存 `id_token`（access token 外的另一條）才能呼，本單 token exchange 時要把 id_token 也存進 authStore
- **F2 簡化未來移除 dual-stack**：JWT path 刪除後，本檔 `tokenSource` field 與 `refresh()` 的 switch 都可移除——這個 cleanup 在 M3.5 ship 後另開 ticket
