# T-021: Frontend — New Character page (mode picker)

**Status:** TODO
**Sprint:** 2
**Est:** S (1h)
**Depends on:** T-008, T-010, T-016 (POST /v1/characters)
**Related:** T-022（送出後跳 session 頁）

---

## Scope

P-03：使用者填名 + 選輸入模式（Template / Reference）→ `POST /v1/characters` → 跳 `/characters/new/session/{session_id}`。

**In scope:**
- Route `/characters/new` (protected)
- `NewCharacterPage` — 1 個 form：
  - `name` 必填，1–50 字（對齊 `planning/data/db-schema.md` line 103 與 T-016 backend validation），即時字數顯示
  - `input_mode` 單選：Template vs Reference（兩張大卡，Template 寫「選單式」、Reference 寫「參考圖」）
  - `[建立]` submit 按鈕，disabled 直到兩欄都填
- React Hook Form + Zod validation
- Submit → `POST /characters`，成功跳 `/characters/new/session/{creation_session.id}`
- 錯誤：`CONFLICT_DUPLICATE_NAME` → 在 name 欄位 inline 顯示「你已有一個同名角色」；其他錯誤 → Toast
- Back 連結：`← 回 Dashboard`
- Vitest：form validation / submit / error inline / success redirect

**Not in scope:**
- Template 的選單控制（在 session 頁，T-022）
- Reference 圖上傳（在 session 頁，T-023）

---

## Planning refs

- `planning/ux/user-flows.md` §4.1 Flow A step 1-2
- `planning/ux/wireframes.md` P-03
- `planning/backend/api-shape.md` §5.1 POST /v1/characters
- `planning/frontend/component-map.md` — NewCharacterPage

---

## Acceptance criteria

- [ ] 未登入 → guard 跳 `/login`
- [ ] Name 空白或 > 50 字 → submit disabled
- [ ] 未選 mode → submit disabled
- [ ] 送出 → 呼 API、成功後跳 session 頁
- [ ] 重名 → inline 錯誤
- [ ] Back link 跳回 `/`
- [ ] `pnpm -C web test -- new-character` 全綠

---

## Files expected to touch

- `web/src/routes/characters/new/route.tsx` (new)
- `web/src/routes/characters/new/NewCharacterPage.tsx` (new)
- `web/src/components/characters/InputModeCard.tsx` (new) — Template / Reference 卡片
- `web/src/hooks/useCreateCharacter.ts` (new) — mutation hook
- `web/src/routes/__root.tsx` (edit)
- `web/src/routes/characters/new/__tests__/` (new)

---

## Notes

- Zod schema：`{ name: string.min(1).max(40), input_mode: z.enum(['template','reference']) }`
- Input mode 卡片：點擊時整張卡 highlight（tailwind `ring-2 ring-primary`）
- Redirect 用 `useNavigate()` 而非 `<Navigate>`（避免 race）
- Submit 期間按鈕顯示 loading spinner + disabled
