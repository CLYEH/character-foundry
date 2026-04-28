# T-020: Frontend — Dashboard Character grid + empty state

**Status:** DONE
**Sprint:** 2
**Est:** S (1h)
**Depends on:** T-008 (auth guard), T-010 (TopNav), T-016 (GET /v1/characters)
**Related:** T-021（Dashboard 的「建立 Character」CTA 跳過去）、T-026（E2E 進入點）

---

## Scope

P-02 Dashboard：登入後預設落地頁。Character grid + empty state + CTA「建立 Character」。使用 TanStack Query 拉 `/v1/characters?owner_id=me`，讀不到就顯示插畫 + CTA。

**In scope:**
- Route `/` (protected)
- `DashboardPage` component — 使用 `useQuery` 拉 character list
- `CharacterCard` component — 縮圖 + 名字 + owner + alias/motion count；click → 跳 `/characters/{id}`
- Empty state：插畫（placeholder SVG 先放，UX 之後換）+ 「還沒有角色，建一個吧」+ Primary CTA `[建立 Character]` → router push `/characters/new`
- Loading state：Skeleton 卡片 × 6
- Error state：`DegradedBanner`（T-010）已經處理全域；本頁 list API error 用 `ErrorBoundary` inline fallback + retry
- Sort 預設 `updated_at DESC`（backend 已預設）
- Non-owner 的卡片：右上顯示 `by {owner}` 小字、Copy 按鈕（Sprint 4 才 wire，Sprint 2 可先 stub disabled with tooltip "Sprint 4 再做"）
- Cursor pagination：捲到底觸發 `fetchNextPage`；首版允許純 `limit=24` 不分頁也行（先不做 infinite scroll，STATUS.md 記 backlog）
- Vitest：render empty state / with items / loading skeleton / click CTA 跳路由

**Not in scope:**
- Search（TopNav 已有 search box，Sprint 5 才 wire）
- Copy 功能本體（Sprint 4）
- 排序切換 UI（MVP 只顯示 updated_at desc）

---

## Planning refs

- `planning/ux/user-flows.md` §2 P-02、§5.4 empty state
- `planning/ux/wireframes.md` Dashboard wireframe
- `planning/frontend/component-map.md` — DashboardPage / CharacterCard
- `planning/backend/api-shape.md` §5.1, §6.1 — list endpoint + DTO

---

## Acceptance criteria

- [ ] 未登入 → 被 guard 跳 `/login`
- [ ] 登入、無 character → 看到 empty state + CTA
- [ ] 登入、有 3 張 character → 看到 grid，排序正確
- [ ] 點卡片 → 跳 `/characters/{id}`
- [ ] 點 CTA → 跳 `/characters/new`
- [ ] Backend 503 → ErrorBoundary fallback + Retry 按鈕
- [ ] `pnpm -C web test -- dashboard` 全綠
- [ ] TypeScript strict、無 `any`（用 `openapi-typescript` 產的 type）

---

## Files expected to touch

- `web/src/routes/dashboard/route.tsx` (new) — React Router v7 route
- `web/src/routes/dashboard/DashboardPage.tsx` (new)
- `web/src/components/characters/CharacterCard.tsx` (new)
- `web/src/components/characters/CharacterGrid.tsx` (new)
- `web/src/components/characters/CharacterGridEmpty.tsx` (new)
- `web/src/components/characters/CharacterGridSkeleton.tsx` (new)
- `web/src/hooks/useCharacterList.ts` (new) — TanStack Query hook
- `web/src/lib/api/characters.ts` (new) — typed API client
- `web/src/routes/__root.tsx` (edit) — 加 `/` route
- `web/src/routes/dashboard/__tests__/` (new)

---

## Notes

- 縮圖用 `<img loading="lazy">`；先寫死 aspect ratio 3:4 避免 CLS
- Character card 的 owner 欄位判斷：`character.owner.id === currentUser.id` 不顯示 "by"，否則顯示
- Grid 用 CSS grid `repeat(auto-fill, minmax(240px, 1fr))`，Tailwind `grid-cols-[repeat(auto-fill,minmax(240px,1fr))]`
- Cursor pagination 先不做 infinite scroll：第一版用 `limit=100` 平鋪，STATUS.md 記 backlog「若 Character 數超過 100 再加 infinite scroll」
- Skeleton 卡片數量固定 6，避免 SSR / layout shift
- `openapi-typescript` 已在 T-007 跑起來，type 從 `web/src/types/openapi.d.ts` import
