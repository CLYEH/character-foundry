# T-088: Wave B miscellany — `task` / `prompt` / `meta` 1:1 wraps

**Status:** TODO
**Sprint:** 3.5b
**Est:** S
**Depends on:** T-080（MCP skeleton）、T-081（registry + CI guardrail）、T-083（endpoint mapping）
**Blocks:** T-084 / T-085 / T-086（三張 packaged tool 都 bundle `GET /v1/tasks/{task_id}` 並 declare `task:read` scope；T-088 是把 `require_scope("task:read")` 補到 task endpoints 的 owner。本單必須先 land，否則 T-081 guardrail 2 對 packaged tool 的 `task:read` scope 找不到 union 來源、reject 整個 registry。詳見 T-083 §6 Q-D6 sequencing note）

---

## Scope

T-083 §6 Q-D6 surfaced：`planning/agent-interface/endpoint-mcp-mapping.md` §2.5 / §2.6 / §2.9 列了 5 個 1:1 MCP tool，但不屬於 T-084（character）/ T-085（alias）/ T-086（motion）任一張 packaged-tool 領域。本單把這 5 條 1:1 wrap 集中落地，避免 Wave B 漏寫。

**In scope（5 條 1:1 tool）：**

- `task.get`：wraps `GET /v1/tasks/{task_id}`，scope `task:read`
- `task.list`：wraps `GET /v1/tasks?status=...&user_id=me`，scope `task:read`
- `task.cancel`：wraps `POST /v1/tasks/{task_id}/cancel`，scope `task:cancel`
- `prompt.preview`：wraps `POST /v1/prompt/preview`，scope `character:read`
- `meta.get`：wraps `GET /v1/meta`，public（no scope required）

### Registry 條目
- 全部 5 條集中在 `app/mcp/tools/task.py` / `app/mcp/tools/prompt.py` / `app/mcp/tools/meta.py`（3 個 module，per namespace 一個檔，per `oauth-mcp-integration.md §3.1`）
- 每個 tool 用 `register(MCPTool(...))` 註冊

### `task.get` 的特殊雙身分
- 在 §2.5 表記為 T-088 owner（單獨 1:1 tool），同時也是 T-084 / T-085 / T-086 packaged-tool 的內部 polling helper。後者**不**算 separate registration —— packaged tool 直接呼 REST endpoint，跟 1:1 tool 的 MCP wrap 是兩件事。
- 本單只負責 1:1 wrap 的註冊；packaged tool 內部 polling 由各自 Wave B ticket 處理。

### `meta.get` + `tools/list` extension（per T-083 §5）
- `meta.get` 1:1 tool 回傳完整 `/v1/meta` payload（models / preset_motions / platform_constraints_version / degraded_services / ...）
- **額外**：`degraded_services` 欄位也透過 MCP `tools/list` response 的 `_meta` extension 露一份（同 Redis-aggregated 來源），讓 agent 不必主動呼 `meta.get` 就能讀到 degraded state
- 兩個 surface 共用一個 backend service（不重複實作）

### 既有 endpoint 補 `require_scope`
- **4 個受保護 endpoint** 若 T-054 落地時未套 → 本單順手套（per S3.5-1 pattern）：`GET /v1/tasks/{task_id}`、`GET /v1/tasks`、`POST /v1/tasks/{task_id}/cancel`、`POST /v1/prompt/preview`
- **加上 `GET /v1/tasks/{task_id}/stream`**（SSE endpoint，T-083 §2.5 表列 owner = T-080，但 T-080 落地時用 `get_current_user_no_pin` 沒套 `require_scope`，留下 scope coverage gap）→ 本單補成 `task:read`，與其它 task endpoint 一致
- **`GET /v1/meta` 保持 public，不套 `require_scope`** —— per `api-shape.md` §5.9 與 §2.9 mapping 行決策（meta 是 health/capability info，必須讓未授權 client 可讀，包含 SPA 啟動時 polling 60s）。本單把 `/v1/meta` 加進 T-081 scope coverage check 的 **explicit public allowlist**（與 `/health` 同 bucket），避免 coverage check 把它當 missing scope。
- T-081 CI guardrail 1 (scope coverage) 會 enforce 上述

### Tests
- `api/tests/mcp/tools/test_task_tools.py`：
  - `task.get` happy path + 404 + scope reject
  - `task.list` filter parameters + scope reject
  - `task.cancel` 4 種 `cancel_outcome`（`cancelled_immediately` / `cancel_pending` / `too_late_completed` / `too_late_failed`）+ 409 already terminal + scope reject
- `api/tests/mcp/tools/test_prompt_tools.py`：
  - `prompt.preview` 3 種 mode (`create_base` / `create_alias` / `create_motion`) + scope reject
- `api/tests/mcp/tools/test_meta_tools.py`：
  - `meta.get` 正常回傳 + degraded_services 非空 case + 公開無需 scope
  - `tools/list` extension 帶 `_meta.degraded_services` 與 `meta.get` 同來源（contract lock-in）

**Not in scope:**
- packaged tool 內部 polling 邏輯（屬於 T-084 / T-085 / T-086）
- task webhook 訂閱（Phase 2 per `scope.md §3`）
- 新增 REST endpoint（純包裝既有）

---

## Planning refs

- `planning/agent-interface/endpoint-mcp-mapping.md` §2.5 / §2.6 / §2.9 / §6 Q-D6（本單 source）
- `planning/agent-interface/open-questions.md` Round 1 Q2（packaging vs 1:1）、Q3（async option A 與 task.get 的關係）
- `planning/backend/oauth-mcp-integration.md` §3（registry pattern）
- `planning/backend/api-shape.md` §5.5（tasks）、§5.6（prompt preview）、§5.9（meta + degraded_services）
- `planning/auth/open-questions.md` §「Q3 canonical scope 字串」

---

## Acceptance criteria

- [ ] 5 條 1:1 tool 全部註冊進 registry（`task.get` / `task.list` / `task.cancel` / `prompt.preview` / `meta.get`）
- [ ] 每個 tool 的 `scopes` 通過 T-081 CI guardrail 2（scope ⊆ union of bundle endpoint scopes）
- [ ] `meta.get` 同時透過 `tools/list` extension 露 `degraded_services`，且兩個 surface 來源一致（同 Redis aggregator）
- [ ] `task.cancel` 4 種 `cancel_outcome` 各一條 e2e test 綠
- [ ] **5 個受保護 endpoint 都套上 `require_scope`**：4 misc-tool endpoint（`GET /v1/tasks/{task_id}` / `GET /v1/tasks` / `POST /v1/tasks/{task_id}/cancel` / `POST /v1/prompt/preview`）+ 1 SSE endpoint（`GET /v1/tasks/{task_id}/stream`，補 T-080 留下的 coverage gap）
- [ ] **`GET /v1/meta` 保持 public，不套 `require_scope`** —— 加進 T-081 scope coverage check 的 explicit public allowlist（與 `/health` 同 bucket）；T-081 scope coverage check 不對它 fail
- [ ] T-081 scope coverage check pass（all protected + explicit-public allowlist 都被認）
- [ ] `pytest api/tests/mcp/tools/test_{task,prompt,meta}_tools.py` 全綠
- [ ] PR description 對照 T-083 §2 表逐條 check（5 條都標 T-088 owner）

---

## Files expected to touch

- `api/app/mcp/tools/task.py` (new) — 3 個 tool（get / list / cancel）
- `api/app/mcp/tools/prompt.py` (new) — 1 個 tool（preview）
- `api/app/mcp/tools/meta.py` (new) — 1 個 tool（get） + tools/list extension hook
- `api/app/mcp/schemas/{task,prompt,meta}.py` (new) — input / output pydantic
- `api/app/api/routes/tasks.py` (edit) — 補 `require_scope`
- `api/app/api/routes/prompt.py` (edit) — 同上
- `api/app/api/routes/meta.py` (edit) — 同上（`/v1/meta` 沒有 scope，但 endpoint coverage check 仍要明確 declare）
- `api/tests/mcp/tools/test_task_tools.py` (new)
- `api/tests/mcp/tools/test_prompt_tools.py` (new)
- `api/tests/mcp/tools/test_meta_tools.py` (new)
- `tickets/T-088-mcp-tool-wave-b-miscellany.md` (new — 本單)
- `STATUS.md` (edit — 完成時 TODO → DONE)

---

## OAuth scope required

本單**不新增 REST endpoint**（純包裝既有），但會**補 `require_scope`** 到既有 endpoint：

| Endpoint | Scope |
|---|---|
| `GET /v1/tasks/{task_id}` | `task:read` |
| `GET /v1/tasks` | `task:read` |
| `GET /v1/tasks/{task_id}/stream` | `task:read` （SSE endpoint；T-080 落地時用 `get_current_user_no_pin`，本單補 `require_scope` 對齊其他 task endpoint） |
| `POST /v1/tasks/{task_id}/cancel` | `task:cancel` |
| `POST /v1/prompt/preview` | `character:read` |
| `GET /v1/meta` | **public，不套 `require_scope`** —— per `api-shape.md` §5.9（health/capability info，未授權 client 必須可讀，SPA 60s polling）。本單把它加進 T-081 scope coverage check 的 explicit public allowlist（與 `/health` 同 bucket） |

決策出處：`planning/agent-interface/endpoint-mcp-mapping.md` §2 / `planning/auth/open-questions.md §「Q3 canonical scope 字串」`

---

## MCP tool delta

**新 tool（5 條全 1:1）：**

| Name | Type | Bundles | Scopes |
|---|---|---|---|
| `task.get` | 1:1 | `GET /v1/tasks/{task_id}` | `task:read` |
| `task.list` | 1:1 | `GET /v1/tasks` | `task:read` |
| `task.cancel` | 1:1 | `POST /v1/tasks/{task_id}/cancel` | `task:cancel` |
| `prompt.preview` | 1:1 | `POST /v1/prompt/preview` | `character:read` |
| `meta.get` | 1:1 + tools/list extension | `GET /v1/meta` | （public）|

決策出處：`planning/agent-interface/endpoint-mcp-mapping.md` §2.5 / §2.6 / §2.9 / §6 Q-D6

---

## Notes

- **為什麼這 5 條湊一張單而非分散進 T-084 / T-085 / T-086**：T-083 §6 Q-D6 的判定 —— task / prompt / meta 是 cross-domain（不屬 character / alias / motion 任一），塞進其中一張會破壞那張 ticket 的 cohesion。T-084 already grew to 10 tools post-Q-D1；硬塞會讓它變 13、scope 失焦。獨立 mini-ticket 是對的切法。
- **為什麼 `task.get` 在 §2.5 同時標 T-088 owner 與 "也被 T-084/T-085/T-086 內部 bundled"**：1:1 tool 註冊與 packaged-tool 內部呼用是兩件事。packaged tool 直接呼 REST endpoint（不透過 MCP layer 上的 task.get tool），所以兩個 wave-B ticket 不衝突 —— 各自負責各自的 surface。`task.get` 的 MCP tool 註冊唯一 owner 是 T-088
- **`task.cancel` 為什麼有獨立 `task:cancel` scope（不是 `task:write`）**：per `auth/open-questions.md §「Q3」` canonical scope 表，cancel 是 destructive 操作但讀 task 同等 surface，獨立 scope 讓 agent 可以「能讀 task 但不能 cancel」。Phase 1 single team 顆粒度足夠
- **`meta.get` 為什麼 public**：`/v1/meta` 暴露 platform constraints / model 版本 / degraded_services，是 client（包含未授權的）必須能讀的 health/capability info，無敏感資料。Endpoint 本身已是 no-auth；MCP tool 對齊
- **`tools/list` extension 的實作 hint**：MCP server 在 `tools/list` response 加 `_meta` extension field，內含 `degraded_services` array（schema 與 `/v1/meta` 同）。Hook 點通常是 server initialization 時 register 一個 `list_tools` middleware，agent 每次 `tools/list` 都拿到當下 state。具體 SDK API 看 `mcp` package；T-080 落地的 dispatcher 應該已有對應 extension point
- **如果 T-088 比 T-084 / T-085 / T-086 早 ship**：好事。1:1 wrap 是後三張的 reference pattern；早一步 land 讓 T-084 / T-085 / T-086 可直接抄 `register(MCPTool(...))` 用法。`task.get` 1:1 wrap 早 land 也讓 packaged tool 的內部 polling 心智模型更清晰（雖然實作上不直接呼 1:1 tool，但 agent surface 一致對應）
