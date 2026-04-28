# T-024: Frontend — Prompt preview modal (M-01)

**Status:** TODO
**Sprint:** 2
**Est:** XS (30m)
**Depends on:** T-019 (preview endpoint), T-022 (session page 已放觸發按鈕)
**Related:** T-026

---

## Scope

M-01「進階檢視」modal：按 T-022 的 `[進階檢視]` 按鈕 → 呼 `POST /v1/prompt/preview` → 顯示 4 個欄位（platform_constraints / menu_fragments / reconciled_note_en / final_prompt）。

**In scope:**
- `PromptPreviewModal` component — shadcn Dialog
- Props：`isOpen`、`onClose`、current form state（mode / menu / note / reference_image_ids / mask）
- 開啟時呼 API，loading spinner 在 modal 內
- 顯示區塊（可各自 copy）：
  1. 「平台固定 constraints」— monospace 顯示
  2. 「選單片段」— 清單
  3. 「重寫後的補述（英文）」— monospace
  4. 「最終 prompt」— monospace、最大、有 copy 按鈕
- 錯誤處理：`VALIDATION_EMPTY_INPUT` 顯示「請先填選項或補述」；`PROMPT_CONFLICT` 顯示 AgentError 的 message + problem + fix
- Close button / ESC / 點背景關閉
- 手機寬度也能看（max-w-2xl）
- Vitest：open/close、呼 API、loading、error、copy 按鈕

**Not in scope:**
- 使用者直接編輯 prompt（**刻意不做**；只能改輸入。見 memory `feedback_hide_implementation_from_user` + functional-scope F-04b）
- Save/share prompt

---

## Planning refs

- `planning/ux/user-flows.md` §2 M-01
- `planning/product/functional-scope.md` §4.1 F-04b
- `planning/backend/api-shape.md` §5.6

---

## Acceptance criteria

- [ ] 點 T-022 的 `[進階檢視]` → modal 開 + API 呼叫一次
- [ ] 4 個區塊 render 正確
- [ ] Final prompt 的 copy 按鈕點一下 → clipboard 寫入 + Toast「已複製」
- [ ] Empty input 錯誤 inline 顯示而非 crash
- [ ] 關閉 modal 後再開 → 重新呼 API（不快取在 client；backend Redis 會 hit cache）
- [ ] `pnpm -C web test -- prompt-preview-modal` 全綠

---

## Files expected to touch

- `web/src/components/creation/PromptPreviewModal.tsx` (new)
- `web/src/hooks/usePromptPreview.ts` (new) — useQuery with `enabled` flag
- `web/src/lib/api/prompt.ts` (new)
- `web/src/components/creation/__tests__/` (edit/new)

---

## Notes

- Modal 不預載；只在使用者點按鈕時才呼 API（省 LLM 費用）
- Clipboard 用 `navigator.clipboard.writeText`（現代瀏覽器支援），fallback 不做
- Monospace 用 Tailwind `font-mono`
- 英文長 prompt 可 `whitespace-pre-wrap` 避免被截斷
