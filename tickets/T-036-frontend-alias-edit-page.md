# T-036: Frontend — Alias edit page (P-06) + InpaintCanvas

**Status:** TODO
**Sprint:** 3
**Est:** M (2h+，react-konva 學習曲線可能拉長)
**Depends on:** T-013（task SSE，前端 useTaskStream 已有）、T-022（reuse useTaskStream / FormField）
**Related:** T-031（後端 endpoint，最終 wire；本單可先用 MSW mock）；T-037（character detail 的 [+ 新增 Alias] 按鈕跳本頁）；T-040（[進階檢視] modal）

---

## Scope

P-06 Alias 編輯頁。三合一輸入（text / image upload / inpaint）合一頁，至少要填一項。Submit → 起 task → SSE 等完成 → 自動 nav 回 character detail。

**In scope:**
- Route `/characters/:id/aliases/new`（protected）
- `AliasEditPage`：
  - 進頁 GET 該 character + base（沿用 `useCharacterDetail`）；無 base → inline 錯誤頁 + Back to Detail
  - Layout per UX §4.2：
    - 左欄：Base 圖（顯示）+ `[啟用 Inpaint]` toggle → 進入 mask 繪製
    - 右欄：Alias 名稱欄、三 checkbox 切換 + 對應 input，[進階檢視]、[生成]、[取消]
- `InpaintCanvas` 元件（react-konva）：
  - 載入 base 圖
  - Brush / eraser 切換、size slider
  - 輸出 PNG bitmap blob（與 base 同尺寸）
  - 顯示 mask 覆蓋比例（% of base image）
  - `[清除 mask]` 重置
  - Mask blob 經 `POST /v1/characters/{id}/aliases/masks`（T-031 提供）拿 `mask_id`
- `ReferenceImageInput`（reuse T-023 的 dropzone / 上傳機制）；上傳後拿 `reference_image_id[]`
- Form：
  - 至少一項驗證：freeform_note OR reference_image_ids OR mask（其一非空）
  - 提交前算出 `input_mode`：純 mask → `inpaint`；純 text → `text`；純 ref → `image`；其他組合 → `mixed`
  - Submit → `POST /characters/{id}/aliases` → 拿 `task_id, alias_id`
  - 開 SSE，loading state（per UX §5.1）
  - completed → toast 「Alias 已建立」+ navigate `/characters/{id}`
  - failed → Layer 2 error toast，停在頁面允許重試
- [進階檢視] 開 prompt preview modal；本單用 placeholder（modal 由 T-040 接），先 noop click handler
- 取消 task：頁面離開（unmount）或使用者按 [取消] → 呼 `POST /tasks/{id}/cancel`，依 `cancel_outcome` toast
- Vitest + MSW：
  - happy path（text-only）
  - inpaint mask 上傳 + submit
  - 三項全空 → 生成按鈕 disabled
  - SSE failed 後仍可再次 submit

**Not in scope:**
- 進階檢視 modal 內容（T-040）
- Multi-alias 列表（T-037 處理 Detail 頁）
- 真實 backend integration test（在 T-031 PR 內）

---

## Planning refs

- `planning/ux/user-flows.md` §4.2 Flow B、§5 互動狀態
- `planning/ux/wireframes.md` P-06
- `planning/frontend/component-map.md` §4.6 InpaintCanvas
- `planning/frontend/async-patterns.md` — SSE / cancel
- `planning/backend/api-shape.md` §5.3 — alias endpoint shape
- `DECISIONS.md` §3 — react-konva for Inpaint

---

## Acceptance criteria

- [ ] 進頁面顯示 base 圖、輸入欄位
- [ ] 三項都空 → [生成] disabled，hint 顯示
- [ ] 純 text submit → MSW 回 task → SSE 完成 → nav 回 detail + toast
- [ ] Inpaint：開啟 mask 模式 → 畫 mask → 顯示覆蓋率 → 清除 mask 重置
- [ ] Inpaint submit 包含 mask blob 上傳 → mask_id 帶進 alias body
- [ ] Reference upload 後 thumbnail 顯示，可移除
- [ ] [取消] 中途 → SSE 收到 cancelled 後 toast「已取消」
- [ ] 失敗後可重試（不需重整）
- [ ] `pnpm -C web test -- alias-edit` 全綠

---

## Files expected to touch

- `web/src/routes/characters/[id]/aliases/new/route.tsx` (new)
- `web/src/routes/characters/[id]/aliases/new/AliasEditPage.tsx` (new)
- `web/src/components/aliases/InpaintCanvas.tsx` (new)
- `web/src/components/aliases/AliasInputPanel.tsx` (new)
- `web/src/components/aliases/MaskPreviewBadge.tsx` (new)
- `web/src/hooks/useCreateAlias.ts` (new)
- `web/src/hooks/useUploadMask.ts` (new)
- `web/src/lib/api/aliases.ts` (new)
- `web/src/routes/__root.tsx` (edit) — register route
- `web/src/routes/characters/[id]/aliases/new/__tests__/` (new)

---

## Notes

- react-konva 安裝：`pnpm -C web add react-konva konva`
- InpaintCanvas 輸出 blob 用 `stage.toCanvas().toBlob()`；alpha channel 為 mask
- Mask 與 base 同尺寸：載入 base 後鎖 stage size = `image.naturalWidth × naturalHeight`，並用 CSS 縮放顯示（`max-width: 100%` + `aspect-ratio`）
- Phase 1 不支援 zoom/pan（如 wireframe 註解「可選」）
- SSE hook reuse `useTaskStream`（T-022 已抽出）
- 本單可先 mock backend，T-031 merge 後 wire 真實 endpoint（測試 update）
- 「Alias 永遠從 Base 生」這條是 backend 約束；frontend 在頁面 header 用文字提示「以 Base 為基底」
