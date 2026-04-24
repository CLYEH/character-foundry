# T-025: Frontend — Select Base + Character Detail (Base-only view)

**Status:** TODO
**Sprint:** 2
**Est:** M (2h)
**Depends on:** T-018 (select-base endpoint), T-016 (GET character detail), T-022
**Related:** T-026（E2E 走完整 flow）；Sprint 3 會在本頁加 Aliases / Motions 區塊

---

## Scope

兩件事：
1. Session 頁上的 `[選作 Base]` 按鈕真的 wire 到 `POST /creation-sessions/{id}/select-base`
2. Character Detail 頁（P-05）Sprint 2 版：顯示 Base + 預留 Aliases / Motions 區塊（empty state）

**In scope:**
- 選作 Base 流程：
  - 點 `[選作 Base]` → 顯示確認對話框「確立 Base 後不可修改，確定？」
  - 確認 → 呼 API → 成功後 `navigate('/characters/{slug}')`
  - 失敗（409 `CONFLICT_BASE_LOCKED` or checkpoint 非 completed）→ Toast 錯誤
- Route `/characters/:slug` （protected）
- `CharacterDetailPage`：
  - `GET /v1/characters/{id}`（slug → id 由 list resolve 或 route param 直接查 id——先接受 slug 並新增 backend endpoint 支援？為避免 backend 改動，本單先改 route 為 `/characters/:id`，由 Dashboard CharacterCard 傳 id）
  - **實際：Route 用 `:slug`，backend T-016 GET endpoint 接受 id。補法：先用 id 當 URL 參數（暫不做 slug 路由），STATUS.md 記 backlog「slug 路由」**
  - Top 區塊：Character 名字、owner、建立時間、`[刪除]`、`[下載 ZIP]`（disabled，Sprint 4）
  - Base 卡片：大圖 + prompt_summary inline + `[查看完整 prompt]`（開 T-024 modal 類似元件）
  - Aliases 區：empty state「Base 是基礎，來加些變體吧」+ `[+ 新增 Alias]`（按鈕 disabled with tooltip「Sprint 3 會開放」）
  - Motions 區：empty state「動作會在這裡出現」+ 5 個 preset placeholder 圖示（disabled）
- 如果 Character.base_id 為 null（session 未 completed）→ redirect 回 `/characters/new/session/{session_id}` 繼續
- Breadcrumb：Dashboard › Character
- Vitest：detail page render、select-base 成功跳轉、confirm dialog、base 為 null 的 redirect

**Not in scope:**
- Alias / Motion 實際功能（Sprint 3）
- Copy button wiring（Sprint 4）
- ZIP download（Sprint 4）
- Slug 路由（先用 id）

---

## Planning refs

- `planning/ux/user-flows.md` §4.1 最後一步、§4.2 Flow B 接入點（但本單只到 empty state）
- `planning/ux/wireframes.md` P-05
- `planning/backend/api-shape.md` §5.1 GET detail、§5.2 select-base
- `planning/product/functional-scope.md` §4.1 F-05

---

## Acceptance criteria

- [ ] 在 session 頁點 `[選作 Base]` → confirm dialog → 確認後 API 成功 → 跳 character detail
- [ ] 若 session 已 completed 仍點 → 收到 409，Toast 顯示，不跳頁
- [ ] Character detail 正確 render：name / base image / owner / created_at / alias count / motion count
- [ ] Aliases 和 Motions 區顯示 empty state，按鈕 disabled with tooltip
- [ ] `base_id === null` → 自動 redirect 回 session 頁
- [ ] 點 `[查看完整 prompt]` → 開 preview 內容（可 reuse T-024 或 inline 一段 read-only view）
- [ ] `pnpm -C web test -- character-detail` 全綠

---

## Files expected to touch

- `web/src/routes/characters/[id]/route.tsx` (new)
- `web/src/routes/characters/[id]/CharacterDetailPage.tsx` (new)
- `web/src/components/characters/BaseCard.tsx` (new)
- `web/src/components/characters/AliasEmptyState.tsx` (new)
- `web/src/components/characters/MotionEmptyStrip.tsx` (new)
- `web/src/components/creation/SelectBaseConfirmDialog.tsx` (new)
- `web/src/hooks/useCharacterDetail.ts` (new)
- `web/src/hooks/useSelectBase.ts` (new)
- `web/src/routes/characters/new/session/CreationSessionPage.tsx` (edit) — wire 確認 dialog + mutation
- `web/src/routes/__root.tsx` (edit)
- `web/src/routes/characters/[id]/__tests__/` (new)

---

## Notes

- Slug backlog：STATUS.md 記「Sprint 3/4 再把 URL 換成 slug」；短期用 id 比較快到 M2
- Confirm dialog 用 shadcn AlertDialog（destructive 語氣）
- 「下載 ZIP」按鈕存在但 disabled；Sprint 4 打開
- 從 session 跳到 detail 要 invalidate character list query（TanStack `queryClient.invalidateQueries`）讓 Dashboard 回去看得到
- 防止 session 被拋棄：`base_id === null && session.status === 'in_progress'` 才 redirect；`abandoned` session 則顯示錯誤頁
