# T-080: MCP server skeleton（FastAPI sub-app `/mcp` + dual-stack auth integration）

**Status:** TODO
**Sprint:** 3.5b
**Est:** M
**Depends on:** T-054（dual-stack middleware；MCP server 直接吃 `require_scope`）、T-053（Authentik client allowlist 已建）
**Related:** T-081（registry pattern 在本單之後落地）、T-082（nginx `/mcp` proxy 並行做）、T-084 / T-085 / T-086（真實 tool 在 Wave B 才開）

---

## Scope

把 MCP server 骨架 mount 進現有 FastAPI app，建立 streamable HTTP transport + 接 dual-stack 驗 token，並用一個 trivial `hello.world` tool 證明 transport + auth + progress notification 三件事都通。

**In scope:**

### MCP Python SDK 依賴
- `api/pyproject.toml` 加 `mcp` 套件，**版本 pin ≥ 包含 PR #2038 的 release**（2026-02-18 merge，修 `ctx.report_progress()` 在 streamable HTTP 下 `related_request_id` 漏帶導致 notification 走錯 stream）
- 鎖版本同時在註解寫明 PR 連結，避免後人不知情下降版

### FastAPI sub-app 掛載
- `app/mcp/app.py` 建立 streamable HTTP MCP server 並以 sub-app 形式 mount 到主 app 的 `/mcp`（per agent-interface Q7 sub-7a same-process 決策）
- `app/main.py` 在既有 `include_router` 區塊後加 `app.mount("/mcp", mcp_app)`
- MCP server 共用主 app 的 DB session factory / `AgentError` schema / task system（不另起 process）

### Token / scope 整合
- MCP server 每個 request 進來時呼共用 `Depends(require_scope(...))` 等價的解 token 邏輯：
  - 走 `app/auth/scopes.py` 的同一條 path 解 `Authorization: Bearer <token>` → JWT 或 OAuth → 拿 `(user, scopes, client_id)`
  - tool 宣告 `scopes=[...]` → MCP server 入口檢查 token 帶足 scope，不足 → MCP error
- M2M（Client Credentials）token 與 delegation（Auth Code + PKCE）token 走同一條解析，差別只在 `user` 是 None vs 有值
- **Allowlist 強制只套 OAuth path**：OAuth token 的 `client_id` 不在 `app/auth/mcp_clients.py` allowlist → MCP error（與 T-054 一致）；**legacy JWT path（dual-stack 期間）不走 allowlist 檢查**——JWT 是既有人 session 的 bearer，沒有 `client_id` 概念，強行套 allowlist 會把 JWT 路徑整條鎖死，違反 T-054 dual-stack 並存的設計。等 JWT path 在 M3.5 ship 後完全移除，allowlist 才會是唯一 gate

### Smoke tool — `hello.world`
- 純為驗證 transport / auth / progress 三件事正常運作
- Input：`{ "echo": str }`
- 行為：sleep 200ms → 透過 `ctx.report_progress(0.5)` 推一條 notification → sleep 200ms → 回 `{ "reply": f"hello, {client_id}: {echo}" }`
- Scope 要求：`character:read`（任何 client 都該至少有這條）
- M3.5c E2E smoke 開單前，這個 tool 不刪——它是 health check

### Tests
- `api/tests/mcp/test_skeleton.py`：
  - 用 MCP Python SDK client 走真 streamable HTTP 連 TestClient mount 起來的 `/mcp`
  - 帶 JWT token 呼 `hello.world` → 200，回 `reply` 含 echo
  - 帶 Authentik OAuth token（用 T-054 conftest 的 `make_oauth_token` 簽）→ 同樣 200
  - 缺 scope（給只有 `task:read` 的 token）→ MCP error
  - 缺 token → MCP error
  - **斷言 progress notification 真的跨 streamable HTTP 抵達 client**（client side 收到至少一條 `notifications/progress` event）——這條是 PR #2038 直接針對的 regression，不靠 SDK 自家 unit test 代驗

**Not in scope:**
- MCP tool registry / `MCPTool` dataclass / CI guardrails（T-081）
- nginx `/mcp` location + proxy_read_timeout（T-082）
- 真實 character / alias / motion tool（Wave B）
- Last-Event-ID resumability（T-087）
- Endpoint MCP whitelist / blacklist enumeration（T-083）

---

## Planning refs

- `planning/agent-interface/open-questions.md` Round 1 Q1（Streamable HTTP）、Q3（Option A 阻塞 + progress notification）
- `planning/agent-interface/open-questions.md` Round 2 Q7 sub-7a（same-process）、sub-7c（client allowlist）
- `planning/agent-interface/open-questions.md` Q3 實作 gotcha 1（SDK 版本 pin）
- `planning/backend/oauth-mcp-integration.md` §2（scope 強制機制）
- `planning/auth/open-questions.md` 決策紀錄 Q8（MCP server 自己驗 token）

---

## Acceptance criteria

- [ ] `pip show mcp` 顯示 ≥ 包含 PR #2038 的版本；`api/pyproject.toml` 內 pin 與註解都有 PR 連結
- [ ] `docker compose up` 後 `curl -i http://localhost/mcp/...` 可拿到 streamable HTTP 響應（具體 path 依 SDK 而定）
- [ ] MCP Python SDK client 用 JWT token 呼 `hello.world` 回 200 + `reply` 含 echo
- [ ] 用 OAuth token（M2M client credentials grant）呼 `hello.world` 同樣 200
- [ ] 缺 `character:read` scope → MCP error；缺 token → MCP error；client_id 不在 allowlist → MCP error
- [ ] Smoke test 真的觀察到 `notifications/progress` event 從 server 抵達 client（不只靠 SDK 自家 unit test）
- [ ] `pytest api/tests/mcp/test_skeleton.py` 全綠
- [ ] 既有 `/v1/*` endpoint regression 不破（dual-stack 仍然運作）

---

## Files expected to touch

- `api/pyproject.toml` (edit) — 加 `mcp` 套件版本 pin + 註解 PR 連結
- `api/app/mcp/__init__.py` (new)
- `api/app/mcp/app.py` (new) — MCP streamable HTTP server 建立
- `api/app/mcp/auth.py` (new) — MCP request → `(user, scopes, client_id)` 解析（reuse `app/auth/scopes.py`）
- `api/app/mcp/tools/__init__.py` (new) — empty placeholder（registry 在 T-081）
- `api/app/mcp/tools/hello.py` (new) — `hello.world` smoke tool
- `api/app/main.py` (edit) — `app.mount("/mcp", mcp_app)`
- `api/tests/mcp/__init__.py` (new)
- `api/tests/mcp/conftest.py` (new) — MCP test client + token helpers（reuse T-054 fixtures）
- `api/tests/mcp/test_skeleton.py` (new)
- `tickets/T-080-mcp-server-skeleton.md` (new — 本單)
- `STATUS.md` (edit)

---

## OAuth scope required

| Endpoint | Scope |
|---|---|
| `POST /mcp/*`（MCP streamable HTTP root） | 由 tool 宣告（`hello.world` → `character:read`） |

> MCP server 本身不是 REST endpoint，scope enforcement 在 tool 層；`require_scope` 由 MCP server middleware 統一處理。

---

## MCP tool delta

**新 tool：**

```python
hello_world = MCPTool(
    name="hello.world",
    description="MCP server smoke tool — echoes input + emits one progress notification.",
    scopes=["character:read"],
    bundles=[],  # 不對應任何 REST endpoint
    input_schema=HelloIn,   # { echo: str }
    output_schema=HelloOut, # { reply: str }
)
```

> 註：bundles 空集合代表此 tool 沒包任何 REST endpoint，純為 transport smoke。T-081 落地 registry 後，這條會是第一個註冊進去的條目。

---

## Notes

- **為什麼 same-process 不是獨立 container**：per agent-interface Q7 sub-7a，docker stack 不多 service / 共用 DB session / 無跨網路 overhead。獨立 container 是 Phase 2 才有意義的優化
- **為什麼 SDK 版本 pin 進 pyproject 而非 lock file**：lock file 會自動算最新解，但 PR #2038 是 minimum requirement——必須在 pyproject 層宣告下限，避免 lock 重生時掉到舊版
- **smoke test 為什麼要真的收 progress notification**：PR #2038 fix 的就是 `related_request_id` 漏帶導致 notification 在 streamable HTTP 下走錯 stream，SDK 自家 unit test 是用 in-memory transport 跑的、不會 reproduce 真實多 client 場景。本單 smoke 用真 streamable HTTP TestClient 連線，確保 notification 真的抵達
- **MCP error vs HTTP status**：MCP server 不回 HTTP 401 / 403——streamable HTTP 連線本身 200，是 inner JSON-RPC envelope 帶 `error` 物件。Auth 失敗時 server 回 MCP error，client 端 SDK 自己解析
- **client_id 從哪來**：OAuth token claim 內帶 `client_id`，allowlist 對它強制；JWT path 是 user 自己的 session（沒有 `client_id` 概念），request context 內 `client_id = None`，allowlist 檢查直接 skip。M3.5 ship 後 JWT path 移除 → 全部 request 都有 `client_id` → allowlist 變成唯一 gate。本單 acceptance criteria「JWT token 呼 `hello.world` 回 200」就是這條 skip 行為的 spec lock-in（Codex review #106 P2 抓到 ticket 早期版本把這條寫得自相矛盾，已 reconcile）
- **dual-stack 為什麼仍要支援 JWT**：M3.5b 期間 SPA 仍可能用 JWT 登入；MCP server 是 agent surface 但 dev 期間可能有人用 SPA 的 JWT token 呼 MCP 驗東西。完整移除 JWT path 是 M3.5 ship 後的 cleanup
