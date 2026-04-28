# Character Foundry — Implementation Status

> **Last updated:** 2026-04-29
> **Phase:** Sprint 1 done（T-006 ~ T-012 全部 done，M1 達成）；Sprint 2 in progress（T-013 ~ T-025 + T-028 done，T-026 ~ T-027 開單中）

---

## Current state

**Planning phase：** ✅ 完成（product / ux / data / backend / frontend / devops 全收斂）
**Implementation phase：** 尚未開工

---

## Sprint progress

### Sprint 0 — Infrastructure
**目標：** `docker compose up` 能跑起整套 stack，hello world 有回應。

| # | Ticket | Status |
|---|---|---|
| T-001 | Repo scaffolding | DONE |
| T-002 | Alembic + initial migrations (teams, users) | DONE |
| T-003 | Remaining migrations (characters → tasks) | DONE |
| T-004 | CI workflow (PR checks) | DONE |
| T-005 | StorageBackend interface + LocalFilesystemBackend | DONE |

### Sprint 1 — Auth + App Shell
**目標：** Login 能成功，看到空 Dashboard。

| # | Ticket | Status |
|---|---|---|
| T-006 | Backend auth (JWT login/refresh/logout/me) | DONE |
| T-007 | Frontend scaffolding (Vite + shadcn init) | DONE |
| T-008 | Frontend auth (login page + store + guard) | DONE |
| T-009 | Backend /health + /v1/meta | DONE |
| T-010 | Frontend TopNav + DegradedBanner | DONE |
| T-011 | Frontend Toast + ErrorBoundary | DONE |
| T-012 | E2E smoke test (login flow) | DONE |

### Sprint 2 — Character Creation
**目標：** 建 Character、選單 / 參考圖模式、Checkpoints、確立 Base（M2）。

| # | Ticket | Status |
|---|---|---|
| T-013 | Backend task queue (arq + Redis) + Task API | DONE |
| T-014 | Backend AI client infra (gpt-image-2 + circuit breaker + stub) | DONE |
| T-015 | Backend Prompt Reconciler module (gpt-5-mini) | DONE |
| T-016 | Backend Character CRUD + CreationSession bootstrap | DONE |
| T-017 | Backend Checkpoint generation flow | DONE |
| T-018 | Backend Select Base / Fork / Abandon | DONE |
| T-019 | Backend Prompt preview endpoint | DONE |
| T-020 | Frontend Dashboard (grid + empty state) | DONE |
| T-021 | Frontend New Character page (mode picker) | DONE |
| T-022 | Frontend Creation Session — template mode | DONE |
| T-023 | Frontend Creation Session — reference mode | DONE |
| T-024 | Frontend Prompt preview modal (M-01) | DONE |
| T-025 | Frontend Select Base + Character Detail (Base only) | DONE |
| T-026 | E2E Character creation smoke test (template) | TODO |
| T-027 | CharacterDetail DTO + frontend resume in-progress session | TODO |
| T-028 | Worker post-lock checkpoint guard（從 T-018 PR #23 拆出來，Codex round-2 P1） | DONE |

### Sprint 3 — Aliases + Motions（尚未開單）
三合一 Alias 輸入（含 Inpaint）、Preset + Custom motion。

### Sprint 4 — Download + Usage（尚未開單）
ZIP 匯出、Copy Character、Usage dashboard。

### Sprint 5 — Polish（尚未開單）
剩餘錯誤處理、E2E coverage、效能調整。

---

## Milestones

- [ ] **M0** — Dev environment runs（`docker compose up` → `/health` returns ok）【Sprint 0 完成】
- [x] **M1** — Login works end-to-end【Sprint 1 完成】
- [ ] **M2** — Create Character (template mode) end-to-end【Sprint 2 完成】
- [ ] **M3** — Aliases + Motions working【Sprint 3 完成】
- [ ] **M4** — Download ZIP works【Sprint 4 完成】
- [ ] **M5** — First internal user feedback【Sprint 5 完成】

---

## 開新 ticket 時更新這張表

- 新單：加進對應 sprint 區塊
- Status 改：同步更新這張表的狀態欄
- 完成：移進 DONE（`git mv`）+ milestone 若符合就勾

---

## Known risks / deferred items

| # | Item | 處理時機 |
|---|---|---|
| M5 | Dropdown 選項實際內容 | 實作時平行填充 |
| M7 | 錯誤 UX 細節訊息 | Frontend 實作時對照真 backend 回應 |
| M8 | Lip sync 延後是未驗證的賭注 | Phase 1 demo 前做 5 人快速 check |
| FB-3 | Storage URL expired 時 backend 要回對的 code | ✅ T-005 完成（`STORAGE_URL_EXPIRED` vs `AUTH_INVALID_TOKEN` 已分開） |
| - | Visual design (Pencil mockup) | 之後需要再開 UX iteration 3 |
| S2-1 | Slug-based URL（目前 `/characters/:id`）| Sprint 3/4 衡量 SEO/可分享性需求再做 |
| ~~S2-2~~ | ~~CharacterDetail DTO 加 `creation_session` 欄位~~ | ✅ 升為 Sprint 2 ticket T-027 |
| S3-1 | `POST /v1/prompt/preview` 的 `mask: {}` 應比照 `reference_image_ids: []` 視為 empty 拒掉（T-019 reviewer 提的 🟡，今天的 `dict[str, Any]` 內部 endpoint 不擋；Sprint 3 inpaint 用真正 `MaskInput` schema 時一起處理）| Sprint 3 alias inpaint ticket 落地時 |
| S2-3 | Dashboard 分頁 / infinite scroll（T-020 首版用 `limit=100` 平鋪，未做 cursor pagination）| Character 數逼近 100 或 UX 反饋時 |
| S2-4 | `Checkpoint` DTO 不含 `menu_selections` / `freeform_note`，所以 server-loaded checkpoint 點 `[用這張再改]` 無法 prefill form（T-022 placeholder 期間靠 client-side 記憶；reload 後就只設 remix base、form 留白）| Backend 加欄位後 Frontend 移除 placeholder fallback |
| S2-5 | `/v1/prompt/preview` 沒有 `base_checkpoint_id` 欄位，所以 remix 模式的 [進階檢視] 沒辦法 faithful 呈現（worker 會以 parent checkpoint 走 image2image + `has_reference_image=True`，preview 端點接到的訊號是 `False`）。T-024 PR #32 codex P2 議題；目前在 frontend disabled 並顯示「remix 模式暫不支援」notice。Backend 加欄位後 frontend 移除 unsupported notice。| 開新 ticket 擴充 `/v1/prompt/preview` schema |
| S2-6 | `BaseDTO` 缺 prompt 欄位（`menu_selections` / `freeform_note` / `prompt_summary`），所以 Character Detail 上的「查看完整 prompt」modal 只能顯示 source checkpoint id + 建立時間，沒辦法重現完整 prompt 組合。T-025 frontend 落地時用 `BasePromptModal` placeholder 暫頂；Backend 在 BaseDTO 加 prompt 欄位後即可改為 reuse PromptPreviewModal。| 開新 ticket 擴充 `BaseDTO` schema |

---

## 下一個 Session 開工前必讀

1. `CLAUDE.md` — 專案定位 + agent 切換
2. `DECISIONS.md` — 核心決策 quick ref
3. `tickets/T-XXX-*.md` — 本單完整內容
4. 單裡 **Planning refs** 列的檔案
