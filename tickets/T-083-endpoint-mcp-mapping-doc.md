# T-083: api-shape §5 endpoint MCP review（whitelist / blacklist / packaging map）

**Status:** TODO
**Sprint:** 3.5b
**Est:** S
**Depends on:** none（純規劃 doc；與 T-080 / T-081 / T-082 並行；產出供 Wave B 每張 tool ticket 直接抄）
**Related:** T-084 / T-085 / T-086（每張 packaged tool 的 bundles 直接從本單表格 copy）

---

## Scope

把 agent-interface Round 3 Q9 拖到 Sprint 3.5b 的 deferred action 落地：對 `planning/backend/api-shape.md` §5.1 ~ §5.9 每個 endpoint 逐條判定「進 MCP / 不進 MCP / 進哪個 packaged tool」，並以 markdown table 形式產出 `planning/agent-interface/endpoint-mcp-mapping.md` 作為 Wave B tool ticket 的權威 input。

**In scope:**

### 新文件 `planning/agent-interface/endpoint-mcp-mapping.md`
- Header 含：產生於 T-083、權威來源指向 `api-shape.md`、更新規則（新增 endpoint 必同步本表）
- §1 分類原則（直接 cite Q9）：
  - ❌ Ops（`/health`、`/v1/meta` 的部分欄位）
  - ❌ Auth（`/v1/auth/*`）
  - ❌ Pure-UI redirect（`/v1/exports/{id}/download`）
  - ✅ Whitelist 範疇（characters / aliases / motions / tasks / usage / meta）
- §2 endpoint-by-endpoint table（每個 endpoint 一列）：

  | Method | Path | Exposed in MCP | Tool / Packaging | Required scope | Reason |
  |---|---|---|---|---|---|
  | `GET` | `/v1/characters` | ✅ | `character.list`（1:1 wrap） | `character:read` | CRUD list |
  | `POST` | `/v1/characters` | ✅ | bundle of `character.create` | `character:write` | session bootstrap step 1 |
  | `POST` | `/v1/creation-sessions/{id}/checkpoints` | ✅ | bundle of `character.create` | `character:write` + `task:read` | session bootstrap step 2 |
  | `POST` | `/v1/creation-sessions/{id}/select-base` | ✅ | bundle of `character.create` | `character:write` | session bootstrap step 3 |
  | `POST` | `/v1/creation-sessions/{id}/reference-images` | ✅ | bundle of `character.create`（reference mode）+ `alias.add`（image / mixed / inpaint mode） | `character:write` | shared upload primitive |
  | `GET` | `/v1/characters/{id}` | ✅ | `character.get`（1:1） | `character:read` | |
  | `GET` | `/v1/characters/{id}/manifest` | ✅ | `character.get_manifest`（1:1） | `character:read` | agent-friendly metadata |
  | `PATCH` | `/v1/characters/{id}` | ✅ | `character.rename`（1:1） | `character:write` | |
  | `DELETE` | `/v1/characters/{id}` | ✅ | `character.delete`（1:1） | `character:write` | |
  | `POST` | `/v1/characters/{id}/restore` | ✅ | `character.restore`（1:1） | `character:write` | |
  | `POST` | `/v1/characters/{id}/copy` | ✅ | `character.copy`（1:1，async） | `character:write` + `task:read` | |
  | `GET` | `/v1/characters/{id}/export` | ✅ | bundle of `character.export`（建立 export → poll task → 取 signed URL） | `character:write` + `task:read` | i2v-tier async |
  | `GET` | `/v1/exports/{id}/download` | ❌ | n/a | n/a | 302 redirect 到 signed URL，agent 直接用 URL 抓 |
  | `POST` | `/v1/checkpoints/{id}/fork` | ✅ | `character.fork`（1:1） | `character:write` | |
  | `GET` | `/v1/creation-sessions/{id}` | ✅ | `character.get_session`（1:1，resume / debug 用） | `character:read` | |
  | `POST` | `/v1/creation-sessions/{id}/abandon` | ✅ | `character.abandon_session`（1:1） | `character:write` | |
  | aliases / motions / tasks / usage / prompt-preview | ... | ... | ... | ... | ... |

  完整表格涵蓋 §5.1 ~ §5.9 全部 endpoint（**本單交付完整版**；上面只是 schema 示意）
- §3 已 packaged tool 與包含 endpoint 對照：
  - `character.create` → 4 個 endpoint（session bootstrap）
  - `alias.add` → 2 個 endpoint（reference-image upload + alias create）
  - `motion.generate` → 1 個 endpoint（polymorphic：base / alias 各一）+ task polling
  - `character.export` → 2 個 endpoint（export trigger + task wait）
- §4 不進 MCP 的 endpoint 與理由：
  - `GET /health`（ops）
  - `/v1/auth/*` 全部（OAuth 接管）
  - `GET /v1/exports/{id}/download`（pure-UI redirect）
  - `GET /storage/{key}`（signed URL serving，agent 直接抓）
- §5 `/v1/meta` 處理：整條進 MCP 但 `degraded_services` 欄位由 MCP `tools/list` extension 也露一份（per agent-interface scope.md §4 互動表）

### Decision log
- 文件末段 §6「待決」清單若有：枚舉過程遇到無法判定的 endpoint（例：未來新增的 webhook 訂閱），列出來 ping 使用者 → 鎖入主表

### 與既有 doc 的雙向 link
- `planning/backend/api-shape.md` §5 開頭加一行：`> Agent 視角的 endpoint → MCP tool 對應見 ../agent-interface/endpoint-mcp-mapping.md`
- `planning/backend/oauth-mcp-integration.md` §3.3 加 reference：「packaging 判斷依本表」

### CI sanity check（optional，可在 T-081 落地時順便加進 lint）
- script 解析 endpoint-mcp-mapping.md 內 markdown table，與 `app/routes/` 實際 endpoint 對照，缺漏 / 多列 → warn（非 hard fail，本單交付 doc 為主）

**Not in scope:**
- 實作 packaging tool（T-084 / T-085 / T-086）
- MCP server 程式碼（T-080）
- 改 api-shape.md 既有 endpoint contract（純標註 reference，不改 spec）

---

## Planning refs

- `planning/agent-interface/open-questions.md` Round 3 Q9（Blacklist by category；本單就是 enumerate 那條 deferred action）
- `planning/agent-interface/scope.md` §4（既有設計互動表）
- `planning/backend/api-shape.md` §5（要逐條 review 的對象）
- `planning/backend/oauth-mcp-integration.md` §3.3（packaging 判斷規則）
- `planning/auth/open-questions.md` §「Q3 canonical scope 字串」（scope 對應）

---

## Acceptance criteria

- [ ] `planning/agent-interface/endpoint-mcp-mapping.md` 新檔建立，含 §1 ~ §6 完整章節
- [ ] §2 endpoint table 涵蓋 `api-shape.md` §5.1 ~ §5.9 **每一條** endpoint（diff 對 `api-shape.md` 該節，不能有遺漏）
- [ ] 每個 ✅ entry 有 tool name 對應；每個 ❌ entry 有理由
- [ ] §3 packaging map 與 §2 table 內各 entry 對應一致（packaged tool 的 bundles 從 §3 反查可以對回 §2）
- [ ] `planning/backend/api-shape.md` §5 開頭已加 reference link
- [ ] `planning/backend/oauth-mcp-integration.md` §3.3 已加 reference link
- [ ] 若有「待決」endpoint 寫入 §6 並在 PR description 點名 ping 使用者
- [ ] T-084 / T-085 / T-086 起單時可直接 cite 本表的 bundle list（PR review 時驗證一致）

---

## Files expected to touch

- `planning/agent-interface/endpoint-mcp-mapping.md` (new)
- `planning/backend/api-shape.md` (edit — §5 header 加 reference)
- `planning/backend/oauth-mcp-integration.md` (edit — §3.3 加 reference)
- `tickets/T-083-endpoint-mcp-mapping-doc.md` (new — 本單)
- `STATUS.md` (edit)

---

## OAuth scope required

`n/a`（純規劃 doc）

---

## MCP tool delta

`n/a`（本單只 enumerate，工具實作在 Wave B；本表交付的 tool name + bundles 就是 Wave B 每張 ticket 的 MCP tool delta 欄位 input）

---

## Notes

- **為什麼是 doc 而非 code constant**：mapping 同時是 spec（給人讀）+ source of truth（給 Wave B ticket 抄 bundles）+ review reference（PR 時對照）。Code constant 重複 doc 內容、且 Wave B ticket 寫的時候沒有 code 可 import。先 doc 後 code 是對的順序
- **為什麼 packaging 判斷不只看「≥2 endpoint」**：oauth-mcp-integration §3.3 的規則是 baseline，但有些 single-endpoint 場景 packaging 也合理（如 `motion.generate` 是 1 個 POST + task polling 是同一個 agent 心智單位）。表格 §2 的 reason 欄位寫清楚每個 packaging 決定的理由
- **既有 endpoint 但未列入 api-shape.md §5 的怎麼處理**：grep `app/routes/` 對照 §5，若有 drift（implementation 多了 endpoint 但 spec 沒寫），本單只列入 mapping 並在 §6 標註「待 api-shape.md 補 spec」，不在本單修 spec
- **未來新 endpoint 怎麼維護本表**：CONTRIBUTING.md 或 `tickets/_TEMPLATE.md` 之後加一條「新增 endpoint 必同步 endpoint-mcp-mapping.md」，本單不改 template（避免 scope 擴散），由後續 process ticket 處理
- **`/v1/auth/*` 為什麼整條黑名單**：OAuth flow 是 human / agent 各自處理（agent 走 client credentials，human 走 SPA），agent 不需要看 login flow；露 `/v1/auth/login` 給 agent 也沒意義（agent 不會 username/password）
- **`character.export` 為什麼 packaging 而非 1:1**：export 是 trigger + 等 task + 拿 signed URL 三步，agent 一條 tool 包完所有交付。download 本身不進 MCP（signed URL agent 拿到後自己 fetch，per §4）
