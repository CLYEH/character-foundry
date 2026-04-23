# T-011: Frontend Toast + ErrorBoundary

**Status:** TODO
**Sprint:** 1
**Est:** S (1h)
**Depends on:** T-007
**Related:** 所有後續 feature 單（都會用 toast 通知 + error handling）

---

## Scope

把 Sonner toast 接進來、建立 ErrorBoundary + 三層錯誤頁面骨架、定義 AgentError → UI mapping 工具函式。

**In scope:**
- Sonner `<Toaster />` 整合進 `AppLayout`
- `toastStore` 完整化（wrapper 封裝 `sonner.toast`，方便換 library）
- `<ErrorBoundary>` component（React 19 原生或 `react-error-boundary` 套件）
- `ErrorPage` composite：各式 Layer 3 錯誤（連線失敗 / 404 / generic 500）
- `ErrorToast` composite：Toast with expand 區塊顯示 `problem / cause / fix / request_id`（Layer 2）
- `AgentError` TypeScript class（對應 backend schema）
- `mapAgentErrorToUI(err)` util：根據 error code prefix 決定該用哪層
- 全域 `AgentError` → Toast 的預設 handler（接到 TanStack Query 的 `onError` 給個預設處理）

**Not in scope:**
- Form inline error handling（表單元件各自整合 RHF 處理，不在這裡）
- 具體錯誤訊息 i18n / 文案（M7 open question，各 feature 實作時補）

---

## Planning refs

- `planning/ux/wireframes.md` §16 Layer 3 錯誤頁、§5.3 三層錯誤規格
- `planning/frontend/async-patterns.md` §7 Error Handling Pipeline
- `planning/frontend/component-map.md` §4.11 ErrorBoundary / ErrorPage
- `planning/backend/api-shape.md` §4 AgentError schema

---

## Acceptance criteria

- [ ] `toast.success(...)` 在右下出現並 2s 消失
- [ ] `toast.error(...)` 可展開看詳情
- [ ] 在 React 組件內 throw → ErrorBoundary 接住，顯示 generic error page
- [ ] 直接 `fetch('/api/404/not-exist')` → 轉 AgentError → ErrorToast 顯示
- [ ] `AgentError.isCategory('VALIDATION_')` 判斷正確
- [ ] `mapAgentErrorToUI` 回 `'inline' | 'toast' | 'page'`（按 error code 前綴分）
- [ ] `pnpm test --run AgentError` + `ErrorBoundary` 綠

---

## Files expected to touch

- `web/src/lib/agentError.ts` (new) — class + mapAgentErrorToUI
- `web/src/stores/toastStore.ts` (edit) — 封裝 Sonner
- `web/src/components/composite/ErrorBoundary/ErrorBoundary.tsx` (new)
- `web/src/components/composite/ErrorPage/ErrorPage.tsx` (new)
- `web/src/components/composite/ErrorPage/NotFoundPage.tsx` (new)
- `web/src/components/composite/ErrorPage/ConnectionErrorPage.tsx` (new)
- `web/src/components/composite/ErrorToast/ErrorToast.tsx` (new)
- `web/src/components/layout/AppLayout.tsx` (edit) — 加 Toaster + ErrorBoundary wrapper
- `web/src/api/queryClient.ts` (edit) — 全域 default error handler
- `web/src/routes/not-found.tsx` (edit) — 用 NotFoundPage composite
- `web/src/lib/agentError.test.ts` (new)

---

## Notes

- Sonner 文件：<https://sonner.emilkowal.ski/>
- AgentError class 保留 backend 欄位名稱一致（snake_case → camelCase 之間做轉換）
- `mapAgentErrorToUI` 規則：
  - `VALIDATION_*` / `CONFLICT_*` → `inline`（由 form handler 決定）
  - `AUTH_EXPIRED` → `page`（redirect login，AuthStore 處理）
  - `AUTH_INVALID_CREDENTIALS` → `inline`（login form）
  - `MODEL_*` / `PROMPT_*` / `STORAGE_*` / `QUOTA_*` → `toast`
  - `INTERNAL_*` → `toast`（嚴重的 500 可觸發 Sentry）
- ErrorBoundary 用 `react-error-boundary`（穩定 library，比手寫好）
- Layer 2 toast 預設 8s 才淡，比 success toast 長
