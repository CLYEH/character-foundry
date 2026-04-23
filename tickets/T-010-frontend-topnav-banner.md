# T-010: Frontend TopNav + DegradedBanner

**Status:** TODO
**Sprint:** 1
**Est:** S (1h)
**Depends on:** T-007, T-008, T-009
**Related:** 每個 Page 之後都會用 TopNav

---

## Scope

建立全域 TopNav（Logo / Search placeholder / UsageWidget placeholder / User menu）與 DegradedBanner，整合進 `AppLayout`。

**In scope:**
- `TopNav` composite：
  - Logo（左，點擊回 `/`）
  - Search input（placeholder，Phase 1 尚未連搜尋 API）
  - UsageWidget（placeholder，顯示「--」）
  - User menu（Avatar + 名字 + dropdown: Settings / Logout）
- `DegradedBanner`：讀 `/v1/meta.degraded_services`，有東西時顯示 warning alert；空陣列不顯示
- `useMeta` hook（TanStack Query，refetch 每 60s）
- `UserMenu` 子元件（含 Logout 邏輯，從 AppLayout 的 stub 搬過來）
- 整合進 `AppLayout`

**Not in scope:**
- 真實 Search 行為（之後 sprint）
- UsageWidget 真實數字（之後 T-xxx 串 `/v1/usage/me`）
- User settings page（T-xxx 之後）

---

## Planning refs

- `planning/ux/wireframes.md` §1.1 TopNav、§1.3 Banner、§2 Login 之後的 TopNav 示範
- `planning/frontend/component-map.md` §4.1 TopNav、§4.9 UsageWidget（placeholder）
- `planning/frontend/async-patterns.md` §8 Degraded Mode Banner

---

## Acceptance criteria

- [ ] 登入後 TopNav 固定在所有 `/`, `/characters/*` 等頁面上方
- [ ] TopNav 顯示 logo、search 框、usage placeholder、使用者名稱
- [ ] 點 User menu 可 Logout（走 T-008 的 logout 流程）
- [ ] `redis-cli SET degraded:gpt-image-2 '{"reason":"CIRCUIT_OPEN",...}'` 後（不等一分鐘，手動 refetch 即可）→ Banner 出現
- [ ] 清掉 Redis key → 下次 refetch Banner 消失
- [ ] `pnpm test --run TopNav` 綠
- [ ] 所有 UX wireframes §1 展示的東西都有（結構對即可，視覺細節 Tailwind + shadcn 給就夠）

---

## Files expected to touch

- `web/src/components/composite/TopNav/TopNav.tsx` (new)
- `web/src/components/composite/TopNav/UserMenu.tsx` (new)
- `web/src/components/composite/TopNav/SearchInput.tsx` (new) — stub
- `web/src/components/composite/TopNav/UsageWidget.tsx` (new) — placeholder
- `web/src/components/composite/TopNav/index.ts` (new)
- `web/src/components/composite/DegradedBanner/DegradedBanner.tsx` (new)
- `web/src/components/composite/DegradedBanner/index.ts` (new)
- `web/src/api/endpoints/meta.ts` (new)
- `web/src/api/queries/useMeta.ts` (new)
- `web/src/components/layout/AppLayout.tsx` (edit) — 放 TopNav + Banner
- `web/src/components/composite/TopNav/TopNav.test.tsx` (new)
- `web/src/components/composite/DegradedBanner/DegradedBanner.test.tsx` (new)

---

## Notes

- TopNav 高度建議 56-64px，預留 sticky top
- DegradedBanner 在 TopNav 下方、主內容上方
- UsageWidget placeholder 顯示 `📊 --` 就好，確保 layout 空間預留
- `useMeta` 的 `refetchInterval: 60_000`，`staleTime` 也 60s
- UserMenu 的 Logout 要把 authStore + localStorage 清乾淨（複用 T-008 的 logout function）
- Search input 先做純 UI，`onSubmit` 先不做事（或 console.log）
