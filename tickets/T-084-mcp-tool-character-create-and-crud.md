# T-084: MCP tool `character.create`（packaged）+ character CRUD 1:1 wraps

**Status:** TODO
**Sprint:** 3.5b
**Est:** M
**Depends on:** T-080（MCP skeleton）、T-081（registry pattern + CI guardrail）、T-083（endpoint mapping doc 提供權威 bundle list）
**Related:** T-085 / T-086（同 Wave B、共用 pattern）、T-087（progress 機制是 Last-Event-ID 的對象）

---

## Scope

Wave B 第 1 張：把 character 領域的 packaged tool（`character.create`）+ 全部 CRUD 1:1 wrap 落地，作為其他兩張 packaged tool（alias / motion）的 reference pattern。

**In scope:**

### Packaged tool — `character.create`
- Bundles（以 T-083 endpoint-mcp-mapping.md §3 表為權威，本單實作時對齊；scope = union of bundle endpoint scopes per `oauth-mcp-integration.md §3.4`）：
  - `POST /v1/characters`（建 character skeleton + creation session）
  - `POST /v1/creation-sessions/{id}/reference-images`（input_mode=reference 才呼）
  - `POST /v1/creation-sessions/{id}/checkpoints`（跑 checkpoint generation；async task）
  - `GET /v1/tasks/{task_id}`（內部 polling 用，等 checkpoint task 跑完；scope `task:read` 從這條進 union）
  - `POST /v1/creation-sessions/{id}/select-base`（鎖死 base）
- Input schema（pydantic）：
  ```python
  class CharacterCreateIn(BaseModel):
      name: str
      input_mode: Literal["template", "reference"]
      menu_selections: dict | None = None      # template mode
      freeform_note: str | None = None
      reference_images: list[bytes] | None = None  # reference mode (multipart 在 MCP 端打包成 base64)
      aspect_ratio: Literal["auto", "1:1", "2:3", "3:2"] = "2:3"
      checkpoint_count: int = 1   # 跑幾次 checkpoint 才 select-base（agent 通常 1 個就 lock）
  ```
- Output schema：`{ character: CharacterDetail, base: Base }`（已 select-base 完成）
- Scopes：`["character:write", "task:read"]`
- Async 模型（per agent-interface Q3 Option A）：
  - Tool 阻塞到完整流程跑完
  - 內部 sub-task（checkpoint generation）跑時用 `ctx.report_progress(...)` 推 MCP `notifications/progress`
  - Progress 訊息包含 phase（`creating_session` / `uploading_references` / `running_checkpoint` / `selecting_base`）+ task progress（0.0 ~ 1.0）+ 估計時間（從既有 task SSE 取）
- Error handling：
  - 任一 sub-step 失敗 → MCP error，含 `phase`（哪一步失敗）+ underlying `AgentError`（`code` / `message` / `fix`）
  - Reference image 上傳失敗 → 已建 session 不會 leak，回 MCP error 同時呼 abandon-session（reuse 既有 endpoint）

### CRUD 1:1 wraps（同 ticket 一起出，share namespace）

僅包現存 REST endpoint（M3 已實作的範圍），per `scope.md §1`「M3.5 不含 ZIP 下載 / Copy / Usage」。被排除的 M4 tools（`character.get_manifest` / `character.copy` / `character.export`）見下方 Not in scope，由 M4 ticket 從 day 1 帶 MCP 條目。

- `character.list`：wraps `GET /v1/characters`，scope `character:read`
- `character.get`：wraps `GET /v1/characters/{id}`，scope `character:read`
- `character.rename`：wraps `PATCH /v1/characters/{id}`，scope `character:write`
- `character.delete`：wraps `DELETE /v1/characters/{id}`（soft delete），scope `character:write`
- `character.restore`：wraps `POST /v1/characters/{id}/restore`，scope `character:write`
- `character.fork`：wraps `POST /v1/checkpoints/{id}/fork`，scope `character:write`
- `character.get_session`：wraps `GET /v1/creation-sessions/{id}`，scope `character:read`（debug / resume 用）
- `character.abandon_session`：wraps `POST /v1/creation-sessions/{id}/abandon`，scope `character:write`
- `character.get_checkpoint`：wraps `GET /v1/checkpoints/{id}`，scope `character:read`（drift from api-shape §5.2 — endpoint exists in code, see T-083 endpoint-mcp-mapping.md §6 Q-D1）

### Registry 條目
- 每個 tool 在 `app/mcp/tools/character.py` 用 `register(MCPTool(...))` 註冊
- 整檔結構參考 T-081 落地的 `hello.py`

### 既有 endpoint 補 `require_scope`
- 上述 endpoint 若 T-054 落地時未套 decorator → 本單順手套（per S3.5-1 「每張碰相關 route 的 ticket 順手清一條」pattern）
- T-081 CI guardrail 1 會 enforce；本單必須讓 scope coverage check pass（不能進 known-allowed 清單）

### Tests
- `api/tests/mcp/tools/test_character_create.py`：
  - template mode：呼 → 收到 `creating_session` / `running_checkpoint` / `selecting_base` 三條 progress notification → 最終回 character + base
  - reference mode：同上 + `uploading_references` phase
  - checkpoint generation 失敗 → MCP error 含 phase=`running_checkpoint` + underlying AgentError
  - reference image 上傳失敗 → abandon session 被呼、無 leak
- `api/tests/mcp/tools/test_character_crud.py`：
  - 每個 CRUD tool 一條 happy path test（8 條）
  - scope 不足 → MCP error

**Not in scope:**
- alias / motion tool（T-085 / T-086）
- Last-Event-ID resumability（T-087；本單實作 progress notification 但不處理斷線重連）
- 改 REST endpoint contract（純包裝既有 endpoint）
- **M4 endpoints — `character.get_manifest` / `character.copy` / `character.export`（含 `character.export` 原本規劃的 packaging）**：對應 REST endpoint（`GET /v1/characters/{id}/manifest` / `POST /v1/characters/{id}/copy` / `GET /v1/characters/{id}/export`）皆未實作，per `STATUS.md` Sprint 4 規劃 + `planning/agent-interface/scope.md §1`「M3.5 不含 ZIP 下載 / Copy / Usage」。M4 ticket 從 day 1 帶 scope decorator + MCP tool 條目（per scope.md §1 段尾）。Codex review #106 round-5 抓到本單原版包了這三個 endpoint，違反「純包裝既有 endpoint」原則，已 reconcile

---

## Planning refs

- `planning/agent-interface/endpoint-mcp-mapping.md`（T-083 交付；bundle list 對齊權威來源）
- `planning/agent-interface/open-questions.md` Round 1 Q2（packaging vs 1:1）、Q3（Option A 阻塞 + progress）
- `planning/backend/oauth-mcp-integration.md` §3（registry pattern）、§3.4（scope ⊆ union of bundle scopes）
- `planning/backend/api-shape.md` §5.1（characters）、§5.2（creation session）
- `planning/auth/open-questions.md` §「Q3 canonical scope 字串」

---

## Acceptance criteria

- [ ] `character.create` packaged tool 註冊進 registry，bundles 與 T-083 §2 表完全一致
- [ ] 全部 9 條 CRUD 1:1 tool 註冊進 registry（`list` / `get` / `rename` / `delete` / `restore` / `fork` / `get_session` / `abandon_session` / `get_checkpoint`；M4-deferred 的 `get_manifest` / `copy` / `export` 不在本單）
- [ ] 每個 tool 的 `scopes` 通過 T-081 CI guardrail 2（⊆ union of bundle endpoint scopes）
- [ ] `character.create` template mode + reference mode 各自一條 e2e test 綠（含 progress notification 驗證）
- [ ] 失敗 path test 綠（checkpoint 失敗、reference upload 失敗、abandon 被呼）
- [ ] 所有 character 領域 endpoint 都套上 `require_scope`，T-081 scope coverage check pass（不放 known-allowed）
- [ ] `pytest api/tests/mcp/tools/test_character_*.py` 全綠
- [ ] PR description 對照 T-083 §2 表逐條 check（每個 bundle / scope / packaging 決定 trace 回 doc）

---

## Files expected to touch

- `api/app/mcp/tools/character.py` (new) — 10 個 tool（1 packaged create + 9 CRUD 1:1；M4-deferred 的 manifest/copy/export 不在本單）
- `api/app/mcp/schemas/character.py` (new) — input / output pydantic schema
- `api/app/api/routes/characters.py` (edit) — 補 `require_scope` decorator（若 T-054 後續未套）
- `api/app/api/routes/creation_sessions.py` (edit) — 同上
- `api/app/api/routes/checkpoints.py` (edit) — 同上
- `api/tests/mcp/tools/__init__.py` (new)
- `api/tests/mcp/tools/test_character_create.py` (new)
- `api/tests/mcp/tools/test_character_crud.py` (new)
- `tickets/T-084-mcp-tool-character-create-and-crud.md` (new — 本單)
- `STATUS.md` (edit)

---

## OAuth scope required

本單**不新增 REST endpoint**（純包裝既有），但會**補 `require_scope`** 到既有 endpoint：

| Endpoint | Scope |
|---|---|
| `GET /v1/characters` | `character:read` |
| `POST /v1/characters` | `character:write` |
| `GET /v1/characters/{id}` | `character:read` |
| `PATCH /v1/characters/{id}` | `character:write` |
| `DELETE /v1/characters/{id}` | `character:write` |
| `POST /v1/characters/{id}/restore` | `character:write` |
| `POST /v1/checkpoints/{id}/fork` | `character:write` |
| `GET /v1/creation-sessions/{id}` | `character:read` |
| `POST /v1/creation-sessions/{id}/checkpoints` | `character:write` |
| `POST /v1/creation-sessions/{id}/reference-images` | `character:write` |
| `POST /v1/creation-sessions/{id}/select-base` | `character:write` |
| `POST /v1/creation-sessions/{id}/abandon` | `character:write` |
| `GET /v1/checkpoints/{id}` | `character:read` |

> ⚠ M4 endpoints（`/v1/characters/{id}/manifest` / `/v1/characters/{id}/copy` / `/v1/characters/{id}/export`）**未實作**，由 M4 ticket 從 day 1 帶 scope decorator + MCP tool 條目（per scope.md §1）

決策出處：`planning/agent-interface/endpoint-mcp-mapping.md` §2

---

## MCP tool delta

**新 tool（10 條 = 1 packaged + 9 CRUD 1:1）：**

| Name | Type | Bundles | Scopes |
|---|---|---|---|
| `character.create` | packaged | 4 endpoints（session bootstrap） | `character:write` + `task:read` |
| `character.list` | 1:1 | `GET /v1/characters` | `character:read` |
| `character.get` | 1:1 | `GET /v1/characters/{id}` | `character:read` |
| `character.rename` | 1:1 | `PATCH /v1/characters/{id}` | `character:write` |
| `character.delete` | 1:1 | `DELETE /v1/characters/{id}` | `character:write` |
| `character.restore` | 1:1 | `POST /v1/characters/{id}/restore` | `character:write` |
| `character.fork` | 1:1 | `POST /v1/checkpoints/{id}/fork` | `character:write` |
| `character.get_session` | 1:1 | `GET /v1/creation-sessions/{id}` | `character:read` |
| `character.abandon_session` | 1:1 | `POST /v1/creation-sessions/{id}/abandon` | `character:write` |
| `character.get_checkpoint` | 1:1 | `GET /v1/checkpoints/{id}` | `character:read` |

決策出處：`planning/agent-interface/endpoint-mcp-mapping.md` §3

---

## Notes

- **為什麼 packaged `character.create` 是 4 個 endpoint 而非 3**：reference mode 才會呼 reference-images endpoint，template mode 跳過。Tool 內部分支，agent 不感知
- **`checkpoint_count` 為什麼 default 1**：agent 場景多數一發即用；UI 場景（生 3-5 個讓人挑）是 human 行為。Agent 要多 checkpoint 時自己加迴圈直接呼 REST endpoint `POST /v1/creation-sessions/{id}/checkpoints`。**Endpoint 本身有兩個用法**：
  - (a) **作為 `character.create` packaged tool 的內部 bundled step（本單範圍）**：建 session → 跑 1 次 checkpoint → select-base，agent 看到的是單一 tool call。當 `checkpoint_count > 1` 時 tool 內部迴圈呼此 endpoint
  - (b) **作為獨立 1:1 MCP tool**（如 `character.add_checkpoint`）：**本單不開**，避免 scope 爆。Agent 要多 checkpoint 一律走 (a) 的 `checkpoint_count` 參數，或自己拿 token 直打 REST endpoint。未來若 agent reveal「想對既有 session 補打 checkpoint 而不重啟整套 packaging」的需求，再另開 ticket 加 1:1 tool
  - 兩個用法並不矛盾：endpoint 在 REST 層永遠存在 + 套 `require_scope`；MCP 層只在 packaging 內呼，不另暴露為 standalone tool（Codex review #106 round-3 P2 抓到本段原文「沒進 packaged tool」與 §Bundles 列「is in packaged」字面矛盾，已 reconcile）
- **progress notification 的 phase 字串約定**：所有 packaged tool 用同一組（`creating_session` / `uploading_references` / `running_checkpoint` / `selecting_base`）。M4 加入 export / copy 時延伸 `exporting` / `copying`
- **失敗 abandon 為什麼是本單而非 REST 層的事**：MCP tool 是「一件事」原子單位，失敗 cleanup 由 tool 內部處理；REST 端有 `POST /v1/creation-sessions/{id}/abandon` 可用，tool 失敗時呼即可，不必改 REST contract
- **CRUD 不另開 `character.list_aliases`**：alias list 屬於 alias 領域，由 T-085 提供 `alias.list`（per character_id 過濾）
- **為什麼 M4-deferred 的 manifest / copy / export 不寫進本單「先包好等 REST 來」**：planning ticket 必須對應 implementable scope；REST 不存在就包 wrapper 等於 CI 永遠 fail（T-081 guardrail 1 scope coverage 會抓不到不存在 endpoint），純包裝原則被破壞。M4 ticket 從 day 1 就會帶 scope decorator + MCP tool 條目（per scope.md §1），M3.5b 不必預埋（Codex review #106 round-5 P1 抓到本單原版包了 3 個未實作 endpoint，已 reconcile）
