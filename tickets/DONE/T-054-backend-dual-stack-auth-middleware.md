# T-054: Backend dual-stack auth middleware（JWT + OAuth 並存）

**Status:** TODO
**Sprint:** 3.5a
**Est:** M
**Depends on:** T-053（要有 Authentik client + JWKS endpoint）、T-055（`refresh_token` 加 `token_source` 欄位）
**Related:** T-056（前端會用本單實作的 Bearer 接收行為）

---

## Scope

讓 backend 同時接受兩種 Bearer token：
1. 既有 JWT（`auth.py` 簽發，dual-stack 期間照常運作）
2. Authentik OAuth access token（Authentik 簽發的 RS256 JWT）

並提供 `require_scope` FastAPI dependency，per backend Step 3 §2 規格。

**In scope:**

### Token 解析
- `Authorization: Bearer <token>` middleware 先看 `iss` claim：
  - 若 `iss == <internal JWT issuer>` → 走既有 JWT verify path
  - 若 `iss == <Authentik issuer URL>` → 走 OAuth verify path（fetch JWKS，verify signature，check audience + exp）
  - 其他 → 401
- OAuth path 把 JWKS 快取進 in-process LRU（TTL 1h），避免每 request 都打 Authentik
- 兩 path 都把 user + scopes 放進 `request.state`，下游 dependency 看不出差別

### `require_scope` dependency
- 實作 `app/auth/scopes.py` 內 `require_scope(*scopes: str)` factory
- 支援 AND（多 scope 都要）：`Depends(require_scope("character:write", "task:read"))`
- OR 走兩個 endpoint 拆，不在 decorator 層處理（per Step 3 §2.3）
- 沒帶 scope → 401；scope 不夠 → 403

### Client_id 驗證
- OAuth path 額外驗 `client_id` 是否在 `app/auth/mcp_clients.py` allowlist 內，否則 403
- M2M token：驗 `scope` claim 是 client 在 allowlist 明示拿到的 subset；超出 → 403（防 Authentik 設錯時越權）

### Tests
- `api/tests/auth/test_dual_stack.py`：JWT path / OAuth path / mixed scope / 過期 / 不在 allowlist 等 case
- 用 `responses` / `httpx_mock` 模擬 Authentik JWKS endpoint
- Fixture 提供 helper：`make_jwt_token(scopes=...)` 與 `make_oauth_token(scopes=..., client_id=...)`

**Not in scope:**
- 把 `require_scope` 套到每個既有 endpoint（不在本單；本單只提供工具，套用在後續 3.5b ticket 隨 endpoint 遷移）
- MCP server 用 token（T-3.5b 第一張）
- Frontend OAuth login（T-056）

---

## Planning refs

- `planning/backend/oauth-mcp-integration.md` §2（Endpoint scope 強制機制）
- `planning/auth/open-questions.md` Q4（簡化 dual-stack）
- `planning/agent-interface/open-questions.md` Q5 sub-5a（scope 模型 + narrow default + 覆寫）
- `planning/agent-interface/open-questions.md` Q7 sub-7c（allowlist 機制）

---

## Acceptance criteria

- [ ] 既有 endpoint 帶 JWT 仍然 200（regression 不破 dual-stack）
- [ ] 帶 Authentik OAuth token 呼 既有 endpoint 200（同樣行為）
- [ ] `require_scope("character:write")` decorator 在帶足 scope 時放行、不足時 403、無 token 401
- [ ] OAuth token client_id 不在 allowlist → 403 with `AUTH_CLIENT_NOT_ALLOWED` AgentError code
- [ ] M2M token scope 超出 allowlist 宣告 → 403 with `AUTH_SCOPE_EXCEEDS_ALLOWLIST`
- [ ] JWKS 快取：第 2 次 OAuth request 不打 Authentik（用 mock 驗證 call count）
- [ ] `pytest api/tests/auth/test_dual_stack.py` 全綠
- [ ] AgentError schema 對齊既有規範（`code` / `message` / `problem` / `cause` / `fix` / `retryable`）

---

## Files expected to touch

- `api/app/auth/oauth.py` (new) — Authentik token verify + JWKS cache
- `api/app/auth/scopes.py` (new) — `require_scope` factory
- `api/app/auth/__init__.py` (edit) — 整合 dual-stack 入 `get_current_user` dependency
- `api/app/auth/errors.py` (edit) — 加新 AgentError codes
- `api/app/auth/mcp_clients.py` (edit — T-053 已建) — 補 helper 函式 `get_allowed_scopes(client_id)`
- `api/tests/auth/test_dual_stack.py` (new)
- `api/tests/auth/conftest.py` (edit) — 加 token fixtures
- `tickets/T-054-backend-dual-stack-auth-middleware.md` (new — 本單)
- `STATUS.md` (edit)

---

## OAuth scope required

本單**不開新 endpoint**，僅實作工具。`n/a`。

> 既有 endpoint 套 `require_scope` 是後續 3.5b ticket 工作，scope 對應見 `planning/backend/api-shape.md` 與 `planning/auth/open-questions.md` Q3。

---

## MCP tool delta

`n/a`（MCP server 在 3.5b 第一張 ticket 建）

---

## Notes

- **為什麼 JWT 用 verify 不用 introspection**：Authentik 用 RS256 簽 JWT，public key 從 JWKS endpoint 拿；verify 是 stateless 不打 Authentik。Introspection 走 `/application/o/introspect/` 多一次 RTT，Phase 1 完全沒必要
- **JWKS cache TTL**：1h 是平衡。Authentik 換 key 不頻繁；若 key rotation 真的發生，stale token 1h 內仍 valid（acceptable risk）。若未來要更嚴，加 Authentik webhook 推 key change 事件
- **`iss` claim 判斷 token 種類**：內部 JWT 與 Authentik token 的 issuer 字串必須完全不同（取 `JWT_ISSUER` env 與 `AUTHENTIK_ISSUER_URL` env，後者形如 `https://auth.character-foundry.local/application/o/character-foundry-spa/`）
- **dual-stack 結束的 hook**：當 `auth.py` JWT path 刪除時，本單 middleware 簡化成 single-path——這條 cleanup 不在本單，會在 M3.5 ship 後另外開
- **TestClient fixture 設計**：`make_oauth_token` 用真 RS256 簽（測試用 keypair 進 conftest），不靠 mock signature verify——確保 production code path 真的跑 verify 邏輯
