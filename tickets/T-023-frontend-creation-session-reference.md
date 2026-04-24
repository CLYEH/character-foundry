# T-023: Frontend — Creation Session reference mode (image upload)

**Status:** TODO
**Sprint:** 2
**Est:** S (1h)
**Depends on:** T-017 (reference-images endpoint), T-022 (session page shell)
**Related:** T-026

---

## Scope

延伸 T-022 的 session 頁：當 `session.input_mode === 'reference'` 時，左欄顯示參考圖上傳區（取代 template menu）。上傳 → 預覽 → 生成時帶 `reference_image_ids`。

**In scope:**
- `ReferenceInputPanel` component — 條件 render（依 session.input_mode）
- Upload UI：
  - 拖放區 + 點擊上傳
  - 多檔（Phase 1 上限 3 張）
  - 即時預覽縮圖 + 刪除
  - 檔案驗證：PNG / JPEG / WebP，單檔 ≤ 10MB（超過直接拒、Toast 提示）
- 呼叫 `POST /creation-sessions/{id}/reference-images`（multipart），收集 `reference_image_id[]`
- Freeform textarea 共用（同 template mode）
- 生成時 payload 改送 `{ mode: 'fresh', reference_image_ids, freeform_note }`（無 menu_selections）
- Remix 模式也要能帶 reference（加 ref img 時更新 form state）
- Action 按鈕與進度條、SSE 處理**全部重用 T-022 的 hook**（不重寫）
- Empty state（無圖無補述）→ `[生成]` disabled
- Vitest：上傳驗證、預覽、刪除、送出 payload 正確

**Not in scope:**
- Alias 的參考圖（Sprint 3 Alias 頁另做）
- 圖片 metadata 編輯

---

## Planning refs

- `planning/ux/user-flows.md` §4.1 Flow A（reference 模式分支）
- `planning/ux/wireframes.md` P-04 reference variant
- `planning/product/functional-scope.md` §4.1 F-03
- `planning/backend/api-shape.md` §5.2 reference-images endpoint

---

## Acceptance criteria

- [ ] `session.input_mode === 'reference'` 時左欄顯示 upload panel（不是 menu）
- [ ] 拖放 3 張 PNG → 3 張 preview 顯示、`reference_image_id[]` 被存入 form state
- [ ] 超過 10MB 檔案 → 拒收 + Toast
- [ ] 超過 3 張 → 第 4 張被拒 + Toast
- [ ] 點刪除 → preview 消失、id 從 state 移除
- [ ] 送出生成 → payload 含 `reference_image_ids` 陣列
- [ ] 空 input → `[生成]` disabled
- [ ] `pnpm -C web test -- reference` 全綠

---

## Files expected to touch

- `web/src/components/creation/ReferenceInputPanel.tsx` (new)
- `web/src/components/creation/ReferenceImageDropzone.tsx` (new)
- `web/src/components/creation/ReferenceImagePreview.tsx` (new)
- `web/src/hooks/useReferenceUpload.ts` (new) — mutation + local state
- `web/src/routes/characters/new/session/CreationSessionPage.tsx` (edit) — 加 branch
- `web/src/lib/api/reference-images.ts` (new)
- `web/src/routes/characters/new/session/__tests__/` (edit)

---

## Notes

- 使用 `react-dropzone` 會帶入相依；先評估是否用原生 drag events；若複雜度高就加 dep
- 上傳前做 client-side MIME sniff（讀前 4 bytes）而非只相信 `File.type`（避免副檔名偽裝；server 本來也會再驗）
- Upload progress 本單不做（Phase 1 單檔 10MB 以內，幾秒內上傳完即可）
- `reference_image_id` vs `reference_image_url`：前端只保留 id 送 backend；預覽用 upload 成功回來的 `url`
- 若上傳失敗：preview 卡片顯示錯誤狀態 + 「重試」
