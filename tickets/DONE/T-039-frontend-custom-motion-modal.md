# T-039: Frontend — Custom motion modal (M-02)

**Status:** TODO
**Sprint:** 3
**Est:** S (1h)
**Depends on:** T-038（generate motion mutation hook 已建）、T-037（MotionRow 結構已存在）
**Related:** T-033（後端 custom motion 端點，本單拉它的 reconciler 路徑）

---

## Scope

Modal M-02 自訂 Motion 對話框：name + description 輸入，submit 後走與 T-038 preset 同套 task / SSE 流程。

**In scope:**
- `CustomMotionModal`（per UX §4.3 wireframe）：
  - 動作名稱（必填，max 50 字、即時字數）
  - 動作描述（必填，max 500 字、即時字數）
  - [取消] / [生成]
  - submit → reuse `useGenerateMotion`（T-038）with body `{ motion_type: 'custom', name, description }`
  - 拿 `task_id, motion_id` → 關 modal → 把新 motion 塞進 MotionRow（自訂區塊）→ cell 進 queued/running 狀態（同 T-038 路徑）
- 觸發：MotionRow 的 `[+ 自訂動作]` 按鈕（T-037 預留 disabled 的那顆，本單 enable 並接 modal）
- 名稱重複（同 parent 內）→ 後端 409，inline error message
- Modal 跳出時 form reset
- Vitest：
  - 名稱 / 描述 必填驗證
  - submit 成功 → modal 關 + 新 motion 出現在自訂區塊
  - 後端 409 → inline error

**Not in scope:**
- Motion rename / delete（T-034 + T-038 已涵蓋）
- 進階 prompt 預覽（custom motion 模式）— 由 T-040 加（本單可預留 [進階檢視] 按鈕但 noop）

---

## Planning refs

- `planning/ux/user-flows.md` §4.3 Flow C、Modal M-02
- `planning/ux/wireframes.md` Modal M-02
- `planning/backend/api-shape.md` §5.4 — custom motion body
- `planning/product/functional-scope.md` §4.3 F-21

---

## Acceptance criteria

- [ ] 點 [+ 自訂動作] → modal 開啟
- [ ] 兩欄空 → [生成] disabled
- [ ] 填完 submit → 後端 200 → modal 關 + cell 出現於自訂區塊 + 進入 queued
- [ ] 重名 → inline error，modal 不關
- [ ] 取消 → modal 關 + form reset
- [ ] `pnpm -C web test -- custom-motion` 全綠

---

## Files expected to touch

- `web/src/components/motions/CustomMotionModal.tsx` (new)
- `web/src/components/motions/MotionRow.tsx` (edit) — 接 modal trigger
- `web/src/components/motions/__tests__/CustomMotionModal.test.tsx` (new)

---

## Notes

- Reuse `useGenerateMotion` 的 mutation hook（T-038 寫的），不要再開一份；body shape 改即可
- 字數即時 hint：用 controlled component + 計算 length；超過上限不擋輸入但 hint 變紅 + submit disabled
- description 維持中文（reconciler 在後端翻；前端不做翻譯）
