# T-081: MCP tool registry + 3 條 CI guardrails

**Status:** TODO
**Sprint:** 3.5b
**Est:** S
**Depends on:** none（與 T-080 / T-082 / T-083 並行；本單只交付 registry pattern + CI script，不依賴 MCP server 已 mount）
**Related:** T-080（registry 由 MCP server 使用）、T-084 / T-085 / T-086（每張新 tool 都吃 registry pattern）、T-053（`mcp_clients.py` 已存在）

---

## Scope

把 `planning/backend/oauth-mcp-integration.md` §3 規定的 `MCPTool` registry 落成 code pattern，並把 §5 列的 3 條 CI / lint 護欄寫成 pre-merge check。讓後續 Wave B 每張 tool ticket 都有可遵循的形狀 + 失誤被擋下來。

**In scope:**

### Registry pattern
- `app/mcp/registry.py`：
  - `MCPTool` dataclass：`name` (str) / `description` (str) / `scopes` (list[str]) / `bundles` (list[str]) / `input_schema` (pydantic BaseModel) / `output_schema` (pydantic BaseModel) / `handler` (callable)
  - module-level `REGISTRY: dict[str, MCPTool]` + `register(tool: MCPTool)` helper
  - import-time discovery：`app/mcp/tools/` 下的每個 module 自動 import 並 register
- `app/mcp/tools/__init__.py`：list 所有 namespace module（character / alias / motion / hello），T-080 的 `hello.world` 改成走 registry 註冊（本單一併 migrate）

### CI guardrail 1 — Scope coverage check
- Script：`api/scripts/check_scope_coverage.py`
- 邏輯：解析 `api/app/api/routes/` 下所有 `@router.<method>(...)` decorator，斷言 handler signature 內有 `Depends(require_scope(...))`
  > ⚠ 真實 route tree 在 `api/app/api/routes/`（不是 `api/app/routes/`）。實作 script 時必須以實際路徑為準，否則 scan 空樹仍會 exit 0、靜默禁用本 gate（Codex review #106 P1 抓到的失誤模式）
- Whitelist（不需要 scope）：`/health`、`/v1/auth/*`、`/storage/*`、`/v1/meta`（per api-shape §5.9 + auth flow）
- 缺漏 → exit 1，列出 file:line + missing endpoint
- 走 `ruff` 或 `mypy` 不適合（這是 semantic check 不是 syntactic），獨立 script
- 進 `.github/workflows/pr.yml` 新 job `scope-coverage`

### CI guardrail 2 — MCP tool scope consistency
- Script：`api/scripts/check_mcp_tool_scopes.py`
- 邏輯：load registry，對每個 `MCPTool`：
  - 解析 `tool.bundles`（格式 `"<METHOD> <PATH>"`）
  - 從 source code grep 對應 endpoint 的 `require_scope(...)` 宣告
  - 斷言 `tool.scopes ⊆ union(endpoint scopes for endpoint in tool.bundles)`
  - 不一致 → exit 1（per oauth-mcp-integration §3.4）
- 也斷言 `tool.scopes ⊆ CANONICAL_SCOPES`（per auth Q3 canonical 5 scope）
- 進 `pr.yml` 同 job 或 sibling job

### CI guardrail 3 — Allowlist consistency
- Script：`api/scripts/check_mcp_clients_allowlist.py`
- 邏輯：load `app/auth/mcp_clients.py` 的 `MCP_CLIENTS` dict，對每個 client：
  - 斷言 `client.allowed_scopes ⊆ CANONICAL_SCOPES`
  - 斷言 `client.default_scopes ⊆ client.allowed_scopes`
- 不合法 scope id → exit 1
- 進 `pr.yml`

### Local lint helper
- `api/scripts/lint_mcp.sh`（或 Makefile target）一次跑三條 check，本地 commit 前可手動跑
- README / CONTRIBUTING 加一行說明（可以在本單一併補）

### Tests
- `api/tests/mcp/test_registry.py`：
  - 註冊一個 dummy tool → 進 REGISTRY → 可被 lookup
  - Tool name 衝突 → raise
- `api/tests/scripts/test_scope_coverage.py` / `test_mcp_tool_scopes.py` / `test_mcp_clients_allowlist.py`：
  - 各自跑 script 對 fixture（合法 / 缺漏 / 超出 allowlist）
  - 正例 exit 0、負例 exit 1 + 含 expected error message

**Not in scope:**
- MCP server skeleton / streamable HTTP transport（T-080）
- nginx config（T-082）
- 真實 character / alias / motion tool（Wave B 才會用本單建立的 pattern 註冊）

---

## Planning refs

- `planning/backend/oauth-mcp-integration.md` §3（MCP tool registry）、§5（CI 護欄三條）
- `planning/auth/open-questions.md` §「Q3 canonical scope 字串」（5 條合法 scope）
- `planning/agent-interface/open-questions.md` Round 2 Q7 sub-7c（allowlist 機制）

---

## Acceptance criteria

- [ ] `app/mcp/registry.py` 的 `MCPTool` dataclass + `register()` + `REGISTRY` dict 可用；import `app.mcp.tools` 後 `hello.world` 已被註冊
- [ ] T-080 的 `hello.world` smoke tool 改成走 registry 註冊，behaviour 不變
- [ ] `python api/scripts/check_scope_coverage.py` 對當前 repo 跑 exit 0（既有 endpoint 該補 require_scope 的，T-054 後續 ticket / 本單若觸及就順手補；本單**主要交付 script + whitelist 機制**，缺漏 endpoint 不在 scope，可加入 known-allowed 清單 + 開 follow-up ticket）
- [ ] `python api/scripts/check_mcp_tool_scopes.py` 對 `hello.world` 跑 exit 0（bundles 空集合 → trivial pass）
- [ ] `python api/scripts/check_mcp_clients_allowlist.py` 對當前 `mcp_clients.py` 跑 exit 0
- [ ] 3 條 script 都有 negative-case test 證明缺漏 / 不一致時 exit 1 + 明確錯誤訊息
- [ ] `.github/workflows/pr.yml` 新增 3 條 check 為 required step（同 job 或 sibling job）
- [ ] `pytest api/tests/mcp/test_registry.py api/tests/scripts/` 全綠
- [ ] PR description 列「known-allowed endpoint」清單（若有），交給後續 ticket 補

---

## Files expected to touch

- `api/app/mcp/registry.py` (new)
- `api/app/mcp/tools/__init__.py` (edit — 加 auto-discover）
- `api/app/mcp/tools/hello.py` (edit — 改走 registry)
- `api/scripts/check_scope_coverage.py` (new)
- `api/scripts/check_mcp_tool_scopes.py` (new)
- `api/scripts/check_mcp_clients_allowlist.py` (new)
- `api/scripts/lint_mcp.sh` (new)
- `.github/workflows/pr.yml` (edit — 加 3 條 check)
- `api/tests/mcp/test_registry.py` (new)
- `api/tests/scripts/__init__.py` (new)
- `api/tests/scripts/test_scope_coverage.py` (new)
- `api/tests/scripts/test_mcp_tool_scopes.py` (new)
- `api/tests/scripts/test_mcp_clients_allowlist.py` (new)
- `CONTRIBUTING.md` (edit — 補一段「MCP tool 加進來時必跑的 lint」)
- `tickets/T-081-mcp-tool-registry-and-ci-guardrails.md` (new — 本單)
- `STATUS.md` (edit)

---

## OAuth scope required

`n/a`（本單不新增 endpoint，只交付 registry pattern + CI script）

---

## MCP tool delta

`n/a`（本單只交付 registry 機制本身；`hello.world` 是從 T-080 migrate 過來，不算新增）

---

## Notes

- **為什麼三條 check 分三個 script 而非合一**：失誤模式不同 → log 不同 → fix 流程不同。一條 script 失敗 banner 寫「以下 3 類問題之一」對使用者 debug 不友善
- **scope coverage 為什麼是 grep-based 而非 import-time check**：FastAPI route decorator 解析需要 import 整個 app，比 grep 慢、且容易被 import-time side effect 影響（pgvector / numpy 等）。Per `STATUS.md` S3.5-2 mutmut 已踩過這條
- **bundles 字串格式 `"METHOD PATH"`**：與 oauth-mcp-integration §3.2 example 一致（如 `"POST /v1/characters"`）；CI check 用 regex 解析
- **canonical scope 字串**：5 條（`character:read` / `character:write` / `task:read` / `task:cancel` / `usage:read`），權威來源是 `app/auth/mcp_clients.py` 的 `CANONICAL_SCOPES` frozenset（T-053 落地）；本單 CI check 直接 import 而非 hardcode 第二份清單
- **既有 endpoint 缺 require_scope 怎麼辦**：T-054 落地 dual-stack middleware 時，scope decorator 沒套到每個 endpoint（其 ticket Not in scope 已明示）。本單 CI check 啟動時應該會列出全部缺漏；正確處理是：(a) 本單 PR description 列清單、(b) 加 known-allowed 機制讓 CI 暫時放行、(c) 後續每張碰相關 route 的 ticket 順手套（同 S3.5-1 leak pattern）。**不在本單 fix 那些缺漏**——scope 會炸開
- **CI 跑哪個 python**：pr.yml 既有 jobs 用 conda env / pip install，本單 3 條 script 用同一個 env 即可（不要再建一個獨立 setup）
