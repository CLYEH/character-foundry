# T-022: Frontend — Creation Session page (template mode)

**Status:** TODO
**Sprint:** 2
**Est:** M (2h)
**Depends on:** T-013 (task SSE), T-017 (checkpoint gen), T-019 (prompt preview), T-021
**Related:** T-023（reference mode 共用本頁）、T-024（Advanced Prompt modal）、T-025（Select Base）

---

## Scope

P-04 Creation Session 頁（template 模式）：左欄輸入控制 + 右欄 Checkpoint 列表。送出生成後用 SSE 即時更新任務狀態，完成後 checkpoint 自動出現在右欄。

**In scope:**
- Route `/characters/new/session/:session_id`
- 進頁面先 `GET /v1/creation-sessions/{id}` 拿 session + 已有 checkpoints
- 左欄 `TemplateInputPanel`：
  - Menu 下拉：性別 / 眼型 / 鼻型 / 髮型 / 膚色 / 體型 / 風格（每項的 option 先 hardcode 在 `menu_options.ts`；backlog 標註之後可從 `/v1/meta` 拉）
  - Freeform textarea（中文，最多 500 字元、即時字數）
  - Actions：`[生成]`、`[重試]`（disabled 直到有 checkpoint）、`[從頭]`（清空 form）、`[進階檢視]`（開 T-024 modal）
  - Context-aware：remix 模式下顯示 “基於 Ckpt #N” 頭，form 預填該 checkpoint 的輸入
- 右欄 `CheckpointList`：
  - 每個 checkpoint 卡片：序號、縮圖、時間戳、status（queued / running / completed / failed）
  - Completed checkpoint：`[用這張再改]` → 設為 remix base + 預填 form；`[選作 Base]` → 進入 T-025 flow
  - Failed checkpoint：顯示錯誤 message（從 `task.error.message`）+ `[重試]`
  - 點縮圖開 lightbox（最小版：fullscreen + prompt_summary）
- Task 生命週期處理：
  - Submit → `POST /creation-sessions/{id}/checkpoints` → 拿 task_id + checkpoint_id
  - checkpoint row 先樂觀塞進 list（status=queued）
  - 開 SSE `/v1/tasks/{task_id}/stream`（用 `@microsoft/fetch-event-source` with JWT header）
  - 狀態 / progress 更新同步到 list 卡片
  - Terminal（completed/failed）：更新 checkpoint row + 關 SSE
- 同時支援多個 checkpoint 排隊（SSE 可以多個並存）
- Cancel：running 中 checkpoint 卡片有 `[取消]` → `POST /tasks/{id}/cancel`，依 `cancel_outcome` 顯示 toast
- Empty state（尚無 checkpoint）：「設定輸入條件，按生成開始」
- Vitest + MSW 模擬 SSE 流程

**Not in scope:**
- Reference mode input（T-023 在本頁加條件 branch）
- Select Base → Character Detail redirect（T-025）
- Advanced prompt modal 本身（T-024）
- Motion / Alias（Sprint 3）

---

## Planning refs

- `planning/ux/user-flows.md` §4.1 Flow A、§5（loading / error states）
- `planning/ux/wireframes.md` P-04
- `planning/frontend/async-patterns.md` — SSE client 實作模板
- `planning/backend/api-shape.md` §3, §5.2 — task SSE + creation-session endpoints
- `planning/frontend/component-map.md` — Creation session 元件
- `DECISIONS.md` §3 — SSE via `@microsoft/fetch-event-source`

---

## Acceptance criteria

- [ ] 進頁面 GET session + checkpoints 成功，list 正確 render
- [ ] 填 menu + freeform → 送出 → list 出現 queued 卡片 → running progress bar 動 → completed 顯示縮圖
- [ ] 同時送出 3 次 → 3 張 running 卡片並列，各自獨立 SSE
- [ ] 點 `[用這張再改]` → form 預填該 ckpt 的輸入、header 變「基於 Ckpt #2」
- [ ] 點 `[重試]` → 用同 input 再發一次
- [ ] 點 `[從頭]` → clear form + unset remix context
- [ ] Running checkpoint 點 `[取消]` → SSE 收到 cancelled 後卡片狀態更新
- [ ] Failed checkpoint 顯示 `error.message`，點 `[重試]` 可重發
- [ ] 進階檢視按鈕可按但內容由 T-024 接；本單只保證 open 事件有觸發
- [ ] `pnpm -C web test -- creation-session` 全綠

---

## Files expected to touch

- `web/src/routes/characters/new/session/route.tsx` (new)
- `web/src/routes/characters/new/session/CreationSessionPage.tsx` (new)
- `web/src/components/creation/TemplateInputPanel.tsx` (new)
- `web/src/components/creation/CheckpointList.tsx` (new)
- `web/src/components/creation/CheckpointCard.tsx` (new)
- `web/src/components/creation/CheckpointLightbox.tsx` (new)
- `web/src/hooks/useCreationSession.ts` (new)
- `web/src/hooks/useTaskStream.ts` (new) — SSE subscription + progress state
- `web/src/hooks/useCreateCheckpoint.ts` (new)
- `web/src/hooks/useCancelTask.ts` (new)
- `web/src/lib/api/checkpoints.ts` (new)
- `web/src/lib/api/tasks.ts` (new)
- `web/src/constants/menu_options.ts` (new) — hardcoded Phase 1 options
- `web/src/routes/__root.tsx` (edit)
- `web/src/routes/characters/new/session/__tests__/` (new)

---

## Notes

- SSE 用 `@microsoft/fetch-event-source` 而非原生 `EventSource`（原生不能帶 header，JWT 過不去）
- Progress bar 規則（UX §6 row 6）：`progress >= 0.05` 顯 bar，否則 indeterminate spinner
- Checkpoint sequence 用 backend 回的，不要前端算（race）
- `useTaskStream` 以 `task_id` 為 key 放在 state map，allow concurrent streams
- 頁面離開時（unmount）記得 abort 所有 SSE connection 避免 leak
- Cancel_outcome 四種都要處理 toast：`cancelled_immediately` → 「已取消」、`cancel_pending` → 「取消中...」、`too_late_*` → 「來不及取消」
- Menu options 檔案裡每個 option 只有 `{ value, label_zh }`；Backend reconciler 負責英譯
