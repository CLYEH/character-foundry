# Character Foundry — Implementation Status

> **Last updated:** 2026-05-08 — T-051 opened: Veo 3.1 RAI filter 走「`done: true` 但無 videos」shape，目前被 `_fetch_video_bytes` 誤分類為 `MODEL_INVALID_REQUEST` 並硬塞「returned 4xx」字串（user 真機 task `371fc9a8` 命中）。Google 已知 RAI false positive 多（`googleapis/js-genai#1272`）。本單會偵測 `raiMediaFilteredCount` / `raiMediaFilteredReasons`、新增 retryable 的 `MODEL_CONTENT_FILTERED`、加 post-submit RAI retry 小預算（env `VEO_RAI_MAX_RETRIES` default 2），並 audit 修掉 `model_invalid_request` template 在非 4xx 路徑硬塞「returned 4xx」的 5 個誤報點。Previous: T-050 merged (#69, commit 05a0ceb) reconciler prompt tuning vs OpenAI image-gen cookbook: SYSTEM_PROMPT全面 rewrite 注入 cookbook 5 大 prompting 原則 (structure / photographic vocab / people hints / literal text / edit preservation)；`platform_constraints.yaml` v1.1 → v1.2 新增 `base_creation_avoid` + `alias_creation_avoid` block; `menu_fragments.py` style 4 個 option 從單行擴成完整描述（lens / lighting / texture）；final prompt 組裝順序改成 cookbook 推薦的 scene → menu → note → avoid。Scope 限定 gpt-image 路徑 (base / alias)，motion / i2v 端 prompt tuning 之後另開單。Cache via `_logic_version` + `constraint_version` 自動失效（含 YAML payload，防止 wording 改動忘 bump version）。Codex review 跑了 3 round（rule-0 reference / YAML hash / double-period strip + period-then-whitespace ordering），都採納。Previous: T-044 closes T-042's last follow-up by adding `tests/ai/test_gpt_image_2_contract.py`.
> **Phase:** Sprint 1 done（T-006 ~ T-012 全部 done，M1 達成）；Sprint 2 done（T-013 ~ T-028 全部 done，M2 達成）；**Sprint 3 done（T-029 ~ T-041，13 張全部 done，M3 達成）**

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
| T-032 | Backend Alias list / detail / rename / delete | DONE |
| T-033 | Backend Motion generation endpoint + worker | DONE |
| T-034 | Backend Motion list / detail / rename / delete | DONE |
| T-035 | Backend Prompt preview extension（alias / motion mode + MaskInput schema）| DONE |
| T-036 | Frontend Alias edit page (P-06) + InpaintCanvas | DONE |
| T-037 | Frontend Character Detail aliases + motions sections | DONE |
| T-038 | Frontend Motion preset generation（click-to-generate + SSE）| DONE |
| T-039 | Frontend Custom motion modal (M-02) | DONE |
| T-040 | Frontend Prompt preview modal extension（alias / motion mode）| DONE |
| T-041 | E2E Alias creation + motion preset smoke（M3 gate）| DONE |
| T-042 | Fix gpt-image API contract on real provider（drop dall-e-3 params + multi-image `image[]`） | DONE |
| T-043 | Sync `planning/backend/ai-integration.md` to real gpt-image contract（T-042 follow-up） | SUPERSEDED by T-048 |
| T-044 | Outgoing-body contract test for gpt-image client（T-042 follow-up） | DONE |
| T-045 | Fix reconciler client for gpt-5-mini contract drift（max_completion_tokens + drop temperature=0）| DONE |
| T-046 | Shared `/storage` volume + nginx `/storage/` proxy（image preview broken bug）| DONE |
| T-047 | Aspect-ratio dropdown + framing guidance（head cropping fix）| DONE |
| T-048 | Sync planning docs（T-042 / T-045 / T-046 / T-047）+ yaml bind-mount in dev override | DONE |
| T-049 | Require e2e happy path for routing / new-page / critical-action PRs（process gate）| DONE |
| T-050 | Reconciler prompt tuning vs OpenAI image-gen cookbook（gpt-image only；i2v 之後另開單） | DONE |
| T-051 | Veo 3.1 RAI filter 偵測 + 修 `model_invalid_request` template 誤導性「returned 4xx」字串 | TODO |

**Dependency / parallelization plan：** 見 `tickets/PARALLEL_WORKFLOW.md`。Wave A（T-029 / T-030 / T-035 / T-036 / T-040）可立即平行開工。

### Sprint 4 — Download + Usage（尚未開單）
ZIP 匯出、Copy Character、Usage dashboard。

### Sprint 5 — Polish（尚未開單）
剩餘錯誤處理、E2E coverage、效能調整。

### Sprint 3.5 — Agent-native baseline（plan phase 完成 2026-05-07，3.5a 已開單）
**目標：** OAuth 2.1（替換 JWT）+ MCP server，外部 agent 不看 REST 文件就能跑全流程。
**規劃：** ✅ 4-step plan phase 全部完成（2026-05-07）。

> **2026-05-12 sequencing 決定（使用者）：** Sprint 3.5a OAuth 系列**整體 blocked on Sprint 3.5-pre harness 全完成**。Harness 蓋完才開始做 M3.5——避免 OAuth + MCP 兩個新 layer 在沒 guardrail 的狀態下落地。詳見 `planning/harness/`。

#### Sprint 3.5-pre — Harness pre-flight（已開單 2026-05-12，未動工）

對照 Martin Fowler "Harness Engineering for Coding Agents"，由 Harness Agent 規劃。完整 rationale 見 `planning/harness/roadmap.md`。

| # | Ticket | Status |
|---|---|---|
| T-058 | Nightly 真 provider contract replay sensor（A1）| TODO |
| T-059 | Architecture fitness — layering / import-direction test（A2）| TODO |
| T-060 | Coverage gate + mutation testing on critical modules（A3）| TODO |
| T-061 | Secret scan + SAST baseline（A4；**T-053 之前必 land**）| TODO |
| T-062 | Subagent stack — security-engineer + db-optimizer（A5）| TODO |
| T-063 | `CF_SKIP_REVIEW=1` audit log（A6）| TODO |

**Dependency / parallelization：**
- T-058 / T-059 / T-060 / T-062 / T-063 五張無內部 dep，可全 wave 平行
- T-061 也無內部 dep，但**對下游 T-053 是 hard blocker**
- 全部 land 後才解 Sprint 3.5a OAuth 系列的 sequencing block

#### Sprint 3.5a — OAuth migration（已開單，未動工；blocked on Sprint 3.5-pre）

| # | Ticket | Status |
|---|---|---|
| T-052 | Authentik docker service 加入 stack | TODO |
| T-053 | Authentik 設定 Google upstream IdP + client 註冊（**Depends on: T-061**） | TODO |
| T-054 | Backend dual-stack auth middleware（JWT + OAuth） | TODO |
| T-055 | `refresh_token` table 加 `token_source` 欄位 | TODO |
| T-056 | Frontend Sign in with Google + AuthCallbackPage + authStore dual-stack | TODO |
| T-057 | E2E OAuth login smoke + dual-stack 並存測試（ship gate） | TODO |

**Dependency / parallelization：**
- 整個 Sprint 3.5a blocked on Sprint 3.5-pre 全完成（2026-05-12 決定）
- 解 block 後：T-052 / T-055 可平行起步（無內部 dep）
- T-053 等 T-052 **且** T-061（A4 secret scan）已 merge；T-054 等 T-055 + T-053
- T-056 等 T-054；T-057 等 T-056

#### Sprint 3.5b / 3.5c — 未開單（3.5a ship 完再開）

**Plan phase deliverable：**
- `planning/agent-interface/open-questions.md` — Round 1/2/3 決策紀錄（9 條全鎖）
- `planning/auth/open-questions.md` — 決策紀錄（8 條全鎖）
- `planning/backend/oauth-mcp-integration.md` — scope decorator + MCP tool registry + CI 護欄
- `planning/frontend/oauth-integration.md` — login UI + authStore dual-stack
- `planning/devops/authentik-stack.md` — Authentik docker stack + persistence
- `tickets/_TEMPLATE.md` — 新增「OAuth scope required」+「MCP tool delta」section

**關鍵決策（high level）：**
- OAuth provider：Authentik (OSS) + Google Workspace 當 upstream IdP
- Grant types：delegation（Auth Code + PKCE）+ M2M（Client Credentials）並存
- Scope：5 條（`character:read/write` / `task:read/cancel` / `usage:read`）+ narrow default + per-client 覆寫
- Signed URL：維持獨立 JWT，與 OAuth 解耦
- MCP transport：streamable HTTP, same-process FastAPI sub-app `/mcp`
- Client 註冊：pre-registered allowlist（Figma 模式），DCR 不開
- Migration：簡化 dual-stack，1 sprint 完成

---

## Milestones

- [ ] **M0** — Dev environment runs（`docker compose up` → `/health` returns ok）【Sprint 0 完成】
- [x] **M1** — Login works end-to-end【Sprint 1 完成】
- [x] **M2** — Create Character (template mode) end-to-end【Sprint 2 完成】
- [x] **M3** — Aliases + Motions working【Sprint 3 完成】
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
