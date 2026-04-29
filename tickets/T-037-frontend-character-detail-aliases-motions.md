# T-037: Frontend — Character Detail aliases + motions sections

**Status:** TODO
**Sprint:** 3
**Est:** M (2h)
**Depends on:** T-025（既有 detail page，empty state 已存在）、T-032（alias list endpoint）、T-034（motion list endpoint）
**Related:** T-036（[+ 新增 Alias] 跳本單登記的 route）；T-038（motion cell 真正能 click-to-generate）；T-040（[查看完整 prompt] modal）

---

## Scope

把 T-025 的 character detail 頁從 "Base only + alias/motion empty placeholder" 升級到「Aliases 列表可見、Motions 區可見」。這單**只做顯示骨架**——cell click → 生成的真實 mutation 由 T-038 / T-039 接；alias create 的 route 跳轉由本單 wire 到 T-036。

**In scope:**
- 擴 `CharacterDetailPage`：
  - Aliases 區：
    - 用 `GET /v1/characters/{id}/aliases` 拉清單
    - 有 alias → 渲染 `AliasRow` 清單（per component-map §4.5）：alias 卡片 + 名稱 + Motion 區（preset 5 + 自訂）+ owner 操作（[編輯名稱] [刪除]）
    - 無 alias → 既有 empty state（T-025 placeholder），但 `[+ 新增 Alias]` 按鈕 enable，導 `/characters/:id/aliases/new`（T-036 route）
  - Motions 區（Base 卡片下）：
    - `MotionRow` + `MotionCell`（per component-map §4.4）
    - 5 個 preset 固定位置：empty (`+`) / completed (縮圖) / failed (`!`)；本單先做 empty + completed render（不接生成 mutation）
    - 已生成的 motion 點縮圖 → lightbox 播放（reuse pattern）
  - 「+ 自訂動作」按鈕存在但 disabled（T-039 接）
- Owner 與 viewer 切換：non-owner 看 alias 但不能加 / 改 / 刪、看 motion 但不能生成 / 刪除（disable + tooltip「僅 owner 可操作」）
- Alias rename / delete inline：
  - rename 用 `PATCH /v1/aliases/{id}`（reuse FormField）
  - delete 用 ConfirmDialog（M-04）+ `DELETE /v1/aliases/{id}`，成功後 invalidate alias query
- Vitest：
  - 多 alias render 正確
  - `[+ 新增 Alias]` 點擊 → navigate 到 alias edit route
  - delete 流程
  - rename 流程
  - non-owner 看不到操作按鈕
  - 含 motions 的 alias 顯示縮圖 + 計數

**Not in scope:**
- Motion cell click → 生成（T-038）
- 自訂 motion 對話框（T-039）
- Motion 刪除（T-038 一起做，與生成 cell 流程關聯較緊）
- 進階檢視 modal（T-040）

---

## Planning refs

- `planning/ux/user-flows.md` §4.2 Flow B 起點、§4.3 Flow C 起點、§5
- `planning/ux/wireframes.md` P-05
- `planning/frontend/component-map.md` §4.4 / §4.5
- `planning/backend/api-shape.md` §5.3 / §5.4
- `planning/product/functional-scope.md` §4.2 / §4.3

---

## Acceptance criteria

- [ ] 多 alias render 正確（含 motion 縮圖）
- [ ] [+ 新增 Alias] 導向 `/characters/:id/aliases/new`
- [ ] 刪除 alias 流程：confirm dialog → API → list 即時更新（invalidate query）
- [ ] 改名 alias 流程：inline edit → API → 列表更新；同 character 重名 → 顯示後端 message
- [ ] Non-owner：alias 操作按鈕全 disabled + tooltip
- [ ] Motion preset cell 在「未生成」時顯示 `+` icon、disabled tooltip「Sprint 3 接續工作會啟用」（T-038 wire 後拿掉）
- [ ] 已存在 motion 縮圖點擊 → lightbox 播放
- [ ] `pnpm -C web test -- character-detail` 全綠（涵蓋既有 + 新加項）

---

## Files expected to touch

- `web/src/routes/characters/[id]/CharacterDetailPage.tsx` (edit)
- `web/src/components/aliases/AliasRow.tsx` (new)
- `web/src/components/aliases/AliasRenameInline.tsx` (new)
- `web/src/components/aliases/AliasDeleteConfirm.tsx` (new)
- `web/src/components/motions/MotionRow.tsx` (new)
- `web/src/components/motions/MotionCell.tsx` (new) — empty + completed states only（本單）
- `web/src/components/motions/MotionLightbox.tsx` (new)
- `web/src/hooks/useAliases.ts` (new) — list + delete + rename
- `web/src/hooks/useMotions.ts` (new) — list only（mutation 留 T-038）
- `web/src/lib/api/aliases.ts` (edit if T-036 已建)
- `web/src/lib/api/motions.ts` (new)
- 測試：`web/src/routes/characters/[id]/__tests__/`（補新 case）+ component 測試

---

## Notes

- AliasRow 內部 reuse MotionRow / MotionCell；alias-bound motions 來源是 `GET /v1/aliases/{alias_id}/motions`
- Motion 5 個 preset 固定位置：前端在 list 結果上補齊 5 個 cell（找不到對應 preset 就用 empty）—— 這條邏輯放 `MotionRow` 內
- T-036 的 alias edit route 必須先在 router tree 註冊，本單可先 stub 一個 route file 跳「Sprint 3 待接」placeholder，T-036 merge 後 placeholder 自動被覆蓋；或本單與 T-036 之間透過 router config 不衝突（routes 是不同 file path）即可平行
- ConfirmDialog 用既有 wrapper（T-021 / T-025 已建）
- Invalidate query keys：`['aliases', characterId]`、`['character', characterId]`（detail 那條）
