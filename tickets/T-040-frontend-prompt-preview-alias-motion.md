# T-040: Frontend — Prompt preview modal extension (alias / motion modes)

**Status:** TODO
**Sprint:** 3
**Est:** S (1h)
**Depends on:** T-024（既有 PromptPreviewModal，僅支援 create_base）、T-035（後端擴 alias / motion mode）
**Related:** T-036（[進階檢視] 按鈕從 alias edit page 觸發）；T-039（[進階檢視] 從 custom motion modal 觸發）；同步收掉 STATUS.md 的 backlog **S2-5**（remix 「暫不支援」notice）

---

## Scope

把 T-024 的 `PromptPreviewModal` 從只認 `create_base` 擴到 `create_alias` 與 `create_motion` 兩個 mode；同時把 T-024 留下的 remix 暫不支援 notice 收掉（依賴 T-035 加上 `base_checkpoint_id` 欄位）。

**In scope:**
- `PromptPreviewModal` props 加 `mode: 'create_base' | 'create_alias' | 'create_motion'` + 對應 input
- 新 view：
  - `create_alias`：顯示 derived_from base 縮圖 + alias input 摘要（freeform_note / refs / mask coverage）+ reconciled prompt
  - `create_motion`：parent 縮圖 + motion_type + reconciled prompt（preset 顯示「使用平台預設模板」）
- T-024 的 `create_base` 路徑加 `base_checkpoint_id` 欄位 → 移除「remix 暫不支援」inline notice，改為正常顯示帶 `has_reference_image=True` 的最終 prompt
- 在 alias edit page（T-036）的 [進階檢視] 按鈕接通 modal
- 在 custom motion modal（T-039）的 [進階檢視] 按鈕接通 modal（preset 不需要 preview 入口；Phase 1 這條由 [進階檢視] 在 alias / custom motion 兩處出現即可）
- Vitest：三 mode 各自 render；空 mask 後端回 422 → modal 顯示 error；preset 不顯示 reconciler block

**Not in scope:**
- Backend prompt-preview 擴充（T-035）
- 修改 final prompt（Phase 1 唯讀）
- Translate origin / 全文 copy 已在 T-024 提供；本單沿用

---

## Planning refs

- `planning/backend/api-shape.md` §5.6 Prompt Preview（含 T-035 新增欄位）
- `planning/frontend/component-map.md` §4.8
- `planning/ux/wireframes.md` Modal M-01
- STATUS.md backlog S2-5（remix 暫不支援 notice）

---

## Acceptance criteria

- [ ] `mode='create_alias'` 顯示 base 縮圖 + alias input + reconciled prompt
- [ ] `mode='create_motion'` preset 不顯示 reconciler block
- [ ] `mode='create_motion'` custom 顯示 reconciled prompt
- [ ] 既有 `create_base` 加上 `base_checkpoint_id` → 不再顯示 「remix 暫不支援」notice
- [ ] mask 422 / 404 → modal 顯示 error message
- [ ] Alias edit page 點 [進階檢視] → modal 開啟（mode=create_alias）
- [ ] Custom motion modal 點 [進階檢視] → modal 開啟（mode=create_motion, motion_type=custom）
- [ ] `pnpm -C web test -- prompt-preview-modal` 全綠（涵蓋既有 + 新加）

---

## Files expected to touch

- `web/src/components/creation/PromptPreviewModal.tsx` (edit) — 三 mode discriminated render
- `web/src/components/aliases/AliasInputPanel.tsx` (edit) — wire [進階檢視]
- `web/src/components/motions/CustomMotionModal.tsx` (edit) — wire [進階檢視]
- `web/src/lib/api/prompt.ts` (edit) — call signature 接 union
- 對應 test files (edit)

---

## Notes

- Discriminated union by `mode` 在 TS 容易：`type Props = BaseProps | AliasProps | MotionProps`，render 內用 `switch (mode)`
- 後端 schema 走 OpenAPI generated types（`openapi-typescript`）；本單用 generated union 而非自寫 type
- 收 S2-5 backlog 後同步在本單 PR 內把 STATUS.md 那列移除
