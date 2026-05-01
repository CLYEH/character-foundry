# Character Foundry — Implementation Status

> **Last updated:** 2026-04-30 — T-033 (backend motion generation endpoint + worker) lands the Veo i2v pipeline end-to-end on top of T-031 (alias generation); Wave B unblocked T-032/T-034.
> **Phase:** Sprint 1 done（T-006 ~ T-012 全部 done，M1 達成）；Sprint 2 done（T-013 ~ T-028 全部 done，M2 達成）；**Sprint 3 開單中（T-029 ~ T-041，13 張，T-029 / T-030 / T-031 / T-033 / T-035 / T-036 / T-040 done）**

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
| T-026 | E2E Character creation smoke test (template) | DONE |
| T-027 | CharacterDetail DTO + frontend resume in-progress session | DONE |
| T-028 | Worker post-lock checkpoint guard（從 T-018 PR #23 拆出來，Codex round-2 P1） | DONE |

### Sprint 3 — Aliases + Motions
**目標：** 三合一 Alias 輸入（含 Inpaint）、Preset + Custom motion，跑完 M3 milestone。

| # | Ticket | Status |
|---|---|---|
| T-029 | Backend Veo 3.1 i2v client + stub | DONE |
| T-030 | Backend gpt-image-2 image2image + inpaint extension | DONE |
| T-031 | Backend Alias generation endpoint + worker | DONE |
| T-032 | Backend Alias list / detail / rename / delete | TODO |
| T-033 | Backend Motion generation endpoint + worker | DONE |
| T-034 | Backend Motion list / detail / rename / delete | TODO |
| T-035 | Backend Prompt preview extension（alias / motion mode + MaskInput schema）| DONE |
| T-036 | Frontend Alias edit page (P-06) + InpaintCanvas | DONE |
| T-037 | Frontend Character Detail aliases + motions sections | TODO |
| T-038 | Frontend Motion preset generation（click-to-generate + SSE）| TODO |
| T-039 | Frontend Custom motion modal (M-02) | TODO |
| T-040 | Frontend Prompt preview modal extension（alias / motion mode）| DONE |
| T-041 | E2E Alias creation + motion preset smoke（M3 gate）| TODO |
| T-042 | Fix gpt-image API contract on real provider（drop dall-e-3 params + multi-image `image[]`） | DONE |

**Dependency / parallelization plan：** 見 `tickets/PARALLEL_WORKFLOW.md`。Wave A（T-029 / T-030 / T-035 / T-036 / T-040）可立即平行開工。

### Sprint 4 — Download + Usage（尚未開單）
ZIP 匯出、Copy Character、Usage dashboard。

### Sprint 5 — Polish（尚未開單）
剩餘錯誤處理、E2E coverage、效能調整。

### Sprint 3.5 — Agent-native baseline（M3 ship 後展開；尚未開單）
**目標：** OAuth 2.1（替換 JWT）+ MCP server，外部 agent 不看 REST 文件就能跑全流程。
**規劃：** `planning/agent-interface/`、`planning/auth/` 已開骨架；open-questions 待 M3 收尾時 review 定案後才開 ticket。

> ⚠ **開 M3.5 任何 ticket 之前必讀** `planning/agent-interface/scope.md` §5「規劃啟動順序」。M3.5 有 17 條未決 open-questions（9 + 8）彼此耦合，必須走完 4-step plan phase（agent-interface → auth → backend → frontend + devops，前 3 步嚴格序列、最後一步並行）才可以開 ticket，否則會邊做邊改大量返工。

---

## Milestones

- [ ] **M0** — Dev environment runs（`docker compose up` → `/health` returns ok）【Sprint 0 完成】
- [x] **M1** — Login works end-to-end【Sprint 1 完成】
- [x] **M2** — Create Character (template mode) end-to-end【Sprint 2 完成】
- [ ] **M3** — Aliases + Motions working【Sprint 3 完成】
- [ ] **M3.5** — Agent-native baseline：OAuth 2.1 + MCP server，外部 agent 能不看 REST 文件跑全流程【2026-04-30 從 Phase 2 拉回 Phase 1；詳見 `planning/agent-interface/`、`planning/auth/`】
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
| S2-3 | Dashboard 分頁 / infinite scroll（T-020 首版用 `limit=100` 平鋪，未做 cursor pagination）| Character 數逼近 100 或 UX 反饋時 |
| S2-4 | `Checkpoint` DTO 不含 `menu_selections` / `freeform_note`，所以 server-loaded checkpoint 點 `[用這張再改]` 無法 prefill form（T-022 placeholder 期間靠 client-side 記憶；reload 後就只設 remix base、form 留白）| Backend 加欄位後 Frontend 移除 placeholder fallback |
| S2-6 | `BaseDTO` 缺 prompt 欄位（`menu_selections` / `freeform_note` / `prompt_summary`），所以 Character Detail 上的「查看完整 prompt」modal 只能顯示 source checkpoint id + 建立時間，沒辦法重現完整 prompt 組合。T-025 frontend 落地時用 `BasePromptModal` placeholder 暫頂；Backend 在 BaseDTO 加 prompt 欄位後即可改為 reuse PromptPreviewModal。| 開新 ticket 擴充 `BaseDTO` schema |
| S3-2 | T-030 `edit_image2image` 多參考圖的 multipart shape（重複 `image` field name）依 gpt-image-1 公開合約建模；gpt-image-2 假設沿用，但需在 T-031 整合真 provider 前以 smoke 驗證一次 | T-031 production cutover 前 |
| S3-3 | Docker stack 與多 worktree 結構性錯位：`docker-compose.yml` 的 `./api/app:/app/app` 等 bind-mount 解析永遠指向主 repo（不論你 cwd 在哪 worktree），且整套 stack 全 worktree 共用一份 container；`docker cp` / `docker exec` 寫 `/app/...` 都會反向洩漏到主 repo 工作樹（2026-04-30 T-033 PR #47 開工時踩過）。`tickets/PARALLEL_WORKFLOW.md` §8 已寫 do/don't + T-031 「`docker run --rm -v $WORKTREE/api:/app`」正確 pattern，但這只是約定，沒結構性阻擋。三個可行修法：(a) 維持文件約定；(b) 改 per-worktree compose project name (`docker compose -p`)；(c) 殺掉 bind-mount source 改 image rebuild（破壞 hot-reload）。| M3.5 開工（OAuth provider docker container 進場時 docker stack 表面擴大）；或 Wave C+ 再有 worktree 踩到時 |

---

## 下一個 Session 開工前必讀

1. `CLAUDE.md` — 專案定位 + agent 切換
2. `DECISIONS.md` — 核心決策 quick ref
3. `tickets/T-XXX-*.md` — 本單完整內容
4. 單裡 **Planning refs** 列的檔案
