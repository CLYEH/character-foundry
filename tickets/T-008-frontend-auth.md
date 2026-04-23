# T-008: Frontend Auth (login page + authStore + protected routes + token refresh)

**Status:** TODO
**Sprint:** 1
**Est:** M (2h)
**Depends on:** T-006, T-007
**Related:** T-010（TopNav 會讀 authStore 顯示使用者）

---

## Scope

Frontend 端完整的 auth：login page + Zustand authStore (含 persist) + protected route guard + token refresh interceptor。

**In scope:**
- `LoginPage`：email + password 表單（React Hook Form + Zod）
- `authStore`：`{ accessToken, refreshToken, user, login(), logout(), updateAccessToken() }` + persist 到 localStorage
- `api/client.ts` 擴充：
  - 注入 `Authorization: Bearer` header
  - 401 時自動 refresh 一次；失敗 → logout + redirect login
  - 單一 flight refresh lock（見 async-patterns.md §6.2）
- Protected route guard：AppLayout loader 檢查 token，無效 redirect `/login?redirect_back={current}`
- Logout 按鈕（暫時放 AppLayout 頂部，T-010 搬進 TopNav）
- Login 成功 → redirect 到 `?redirect_back` 或 `/`

**Not in scope:**
- Forgot password（Phase 1 不做）
- 記住我（localStorage 已經持續，不需要 UI toggle）

---

## Planning refs

- `planning/frontend/architecture.md` §3 Routing、§4.2 authStore
- `planning/frontend/async-patterns.md` §1 API client、§6 Token refresh
- `planning/backend/api-shape.md` §2 Auth endpoints
- `planning/ux/wireframes.md` §2 Login page layout

---

## Acceptance criteria

- [ ] 未登入訪問 `/` → redirect `/login?redirect_back=%2F`
- [ ] Login 正確 credential → redirect 到 `redirect_back` 或 `/`
- [ ] Login 錯密碼 → inline 錯誤「Email 或密碼錯誤」（不揭露哪個）
- [ ] 登入後 `localStorage.getItem('cf-auth')` 有 token
- [ ] Access token 過期（手動 localStorage 改一下 exp）→ 自動 refresh + 原 request retry 成功
- [ ] Refresh token 也失效 → redirect `/login` + localStorage 清掉
- [ ] 點 Logout → token 清掉 + redirect login
- [ ] `pnpm test --run src/stores/authStore.test.ts` 綠
- [ ] Playwright 能完成 login → `/` 看到 stub（T-012 跑）

---

## Files expected to touch

- `web/src/routes/login.tsx` (edit) — 實作 LoginPage
- `web/src/stores/authStore.ts` (edit) — Zustand + persist middleware
- `web/src/api/client.ts` (edit) — JWT header + 401 refresh
- `web/src/api/endpoints/auth.ts` (new) — `login`, `refresh`, `logout`, `getMe` 呼叫函式
- `web/src/api/mutations/useLogin.ts` (new)
- `web/src/api/queries/useMe.ts` (new)
- `web/src/components/layout/AppLayout.tsx` (edit) — 加 auth guard + logout button stub
- `web/src/components/composite/LoginForm/LoginForm.tsx` (new)
- `web/src/lib/validators.ts` (edit，若無則 new) — Zod login schema
- `web/src/stores/authStore.test.ts` (new)
- `web/src/api/client.test.ts` (new) — 401 refresh flow

---

## Notes

- `authStore` 用 Zustand `persist` middleware，key = `cf-auth`
- Refresh in-flight deduplication 用 module-level Promise（見 async-patterns.md §6.2）
- `redirect_back` encode URL 再帶上
- LoginPage 不要用 shadcn `Form` 元件（還沒 add），改用 plain `<form>` + shadcn `Input` / `Button`
- 錯誤訊息 i18n 先 hardcode 中文；未來有 i18n 再抽
- Logout 同時打後端 `/auth/logout`（revoke refresh）**和** 清 localStorage（雙保險）
