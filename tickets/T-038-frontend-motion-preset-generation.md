# T-038: Frontend — Motion preset generation (click-to-generate + SSE)

**Status:** TODO
**Sprint:** 3
**Est:** M (2h)
**Depends on:** T-013（task SSE）、T-033（後端 motion endpoint）、T-037（MotionRow / MotionCell 已建立）
**Related:** T-039（自訂 motion modal 共用本單寫的 motion mutation hook）；T-034（list / delete endpoint）

---

## Scope

把 T-037 的 MotionCell 從「empty + completed only」升級到「全 5 種狀態」，並接通 click-to-generate flow。同時補上 motion 刪除（cell 級別的 [⋯] menu）。

**In scope:**
- `MotionCell` 5 種狀態：
  - empty (`+` icon, 點擊觸發生成)
  - queued（spinner + queue position 若有）
  - running（progress bar，per UX §5.1）
  - completed（影片縮圖 + hover 顯示 `[⋯]` menu）
  - failed (`!` + error tooltip + [重試] button)
- 點 empty `+`：
  - 對應 preset_motion_type
  - `POST /v1/bases/{base_id}/motions` 或 `POST /v1/aliases/{alias_id}/motions` body `{ motion_type: 'preset_*', name: 'preset 中文名' }`
  - 拿 `task_id, motion_id`，把 cell 切到 queued/running，開 SSE
  - completed → cell 切 completed + 縮圖 + 影片 url
  - failed → cell 切 failed
- 多 cell 同時生成：每個 cell 自己一條 SSE（`useTaskStream` map by motion_id）
- Cancel：running cell 點 [取消] → `POST /tasks/{id}/cancel`
- Motion `[⋯]` menu（completed 狀態）：[重新播放] / [刪除]
  - 刪除：confirm dialog → `DELETE /v1/motions/{id}` → invalidate motions query
- Owner-only：non-owner 看到 cell 但所有 trigger 都 disabled
- Vitest：
  - 點 empty → MSW 模擬 task → SSE 走完 → cell 變 completed
  - 同時觸發 3 個 cell → 各自獨立 SSE
  - failed 顯示錯誤 + 點 [重試] 重發
  - delete cell 流程
  - 已生成 preset 再點不會觸發新 task（cell 已不可 click）

**Not in scope:**
- 自訂 motion modal（T-039）
- Motion rename（T-034 backend 提供，UI 在 T-037 alias rename 同 pattern 即可—不在本單）
- Lip sync（Phase 1 暫緩）

---

## Planning refs

- `planning/ux/user-flows.md` §4.3 Flow C
- `planning/ux/wireframes.md` P-05 motion 區
- `planning/frontend/component-map.md` §4.4 / §4.10
- `planning/frontend/async-patterns.md`
- `planning/backend/api-shape.md` §5.4

---

## Acceptance criteria

- [ ] 點 base 的 preset_wave 空 cell → task → SSE → cell 變 completed + 縮圖
- [ ] 點 alias 的 preset_nod 同流程
- [ ] 並行 3 個 preset → 3 個 SSE，獨立進度
- [ ] running 點 [取消] → cell 切 cancelled，re-enable 為 empty
- [ ] failed 顯示 error message，點 [重試] 重發 task
- [ ] completed `[⋯]` → [刪除] → confirm → DELETE → cell 回 empty
- [ ] Non-owner：所有 trigger disabled + tooltip
- [ ] `pnpm -C web test -- motion-cell` 全綠

---

## Files expected to touch

- `web/src/components/motions/MotionCell.tsx` (edit) — 補 queued / running / failed states
- `web/src/components/motions/MotionGenerateButton.tsx` (new) — 包 preset / parent 對應的 trigger 邏輯
- `web/src/components/motions/MotionDeleteConfirm.tsx` (new)
- `web/src/hooks/useGenerateMotion.ts` (new)
- `web/src/hooks/useDeleteMotion.ts` (new)
- `web/src/hooks/useMotions.ts` (edit) — 加 mutation；list 已在 T-037
- `web/src/lib/api/motions.ts` (edit)
- `web/src/components/motions/__tests__/` (new)

---

## Notes

- Preset 中文名對照表：招手歡迎 / 點頭說明 / 手勢指引 / 開心回應 / 靜置待機（per F-20）—— 放 `web/src/constants/preset_motions.ts`
- 影片 lightbox（T-037 已建 MotionLightbox）reuse；本單保證點 completed cell 開得起來
- progress bar 規則延用 T-022 約定：progress >= 0.05 顯 bar，否則 indeterminate spinner
- Queue position 若 SSE 有給就顯示「#3 in queue」，沒有就 indeterminate spinner（per UX §5.1）
- `useTaskStream` 是既有 hook（T-022 抽出）—— 一個 motion_id 對應一條 SSE，unmount 時 abort
- Motion 與 alias 共用 ParentRef pattern：`{ type: 'base'|'alias', id }`，在 hook 內拼 endpoint URL
