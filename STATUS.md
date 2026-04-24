# Character Foundry — Implementation Status

> **Last updated:** 2026-04-24
> **Phase:** Sprint 1 in progress（T-006 done）

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
| T-007 | Frontend scaffolding (Vite + shadcn init) | TODO |
| T-008 | Frontend auth (login page + store + guard) | TODO |
| T-009 | Backend /health + /v1/meta | TODO |
| T-010 | Frontend TopNav + DegradedBanner | TODO |
| T-011 | Frontend Toast + ErrorBoundary | TODO |
| T-012 | E2E smoke test (login flow) | TODO |

### Sprint 2 — Character Creation（尚未開單）
建 Character、選單 / 參考圖模式、Checkpoints、確立 Base。

### Sprint 3 — Aliases + Motions（尚未開單）
三合一 Alias 輸入（含 Inpaint）、Preset + Custom motion。

### Sprint 4 — Download + Usage（尚未開單）
ZIP 匯出、Copy Character、Usage dashboard。

### Sprint 5 — Polish（尚未開單）
剩餘錯誤處理、E2E coverage、效能調整。

---

## Milestones

- [ ] **M0** — Dev environment runs（`docker compose up` → `/health` returns ok）【Sprint 0 完成】
- [ ] **M1** — Login works end-to-end【Sprint 1 完成】
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

---

## 下一個 Session 開工前必讀

1. `CLAUDE.md` — 專案定位 + agent 切換
2. `DECISIONS.md` — 核心決策 quick ref
3. `tickets/T-XXX-*.md` — 本單完整內容
4. 單裡 **Planning refs** 列的檔案
