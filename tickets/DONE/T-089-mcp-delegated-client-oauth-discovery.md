# T-089: MCP delegated-client OAuth discovery (PRM / RFC 9728 + auto-login)

**Status:** DONE（2026-06-08 — 實作 + CI-verifiable ACs 綠；AC #3 真 client end-to-end 為 Manual，待 operator 跑 MCP Inspector）
**Sprint:** post-M3.5（使用者 2026-05-21 拍板「要支援真人用 MCP client 連進來 + 自動登入」；不擋 3.5b 收尾 / 3.5c headless-agent E2E）
**Est:** M（含 plan phase；需 agent-interface + auth 兩視角）
**Depends on:** ✅ S3.5-6 **RESOLVED by T-093**（2026-06-08 — Authentik 的 5 條 scope-mapping expression 改成 `return {"scope": " ".join(token.scope)}`，delegated + M2M token 現在都把 5 條 app scope 發進 JWT `scope` claim；root cause 是 expression 原本是 `return {}`，不是 consent / attachment 問題）。本單的 hard-dep 已清；剩下 `/mcp/` discovery（PRM / `WWW-Authenticate`）本身。
**Related:** T-080（MCP server skeleton + dual-stack bearer）、T-053（Authentik client 註冊）、T-056（SPA OAuth login UI）

---

## Scope

讓**真人驅動的 MCP client**（Claude Desktop / Cursor / claude.ai remote connector 等）連到 `/mcp/` 時，能**自己走 OAuth 2.1 discovery → 把人導去登入頁（Authentik / Google）→ 取得 delegated token → 用它呼叫 character.* 工具**，不需要使用者手動貼 Bearer token。

這是 M3.5「agent-native」願景裡**真人 delegated** 那一半；headless / M2M（client_credentials）那一半 T-080 已做完。Authentik 當初選型就是為了能 host PRM（`planning/auth/open-questions.md` Q1 明列「Google Identity 無法 host PRM」當不選的理由），這張單把那個預留能力接起來。

**In scope:**
- MCP server 實作 **Protected Resource Metadata（RFC 9728）**：`GET /.well-known/oauth-protected-resource`（mount 在 `/mcp` 旁或 api root），指向 Authentik 當 authorization server + 宣告需要的 scope（5 條 canonical）。
- **401 回 `WWW-Authenticate: Bearer resource_metadata="..."`** —— 沒帶 token 的 MCP 請求觸發 client 的 discovery（目前 `app/mcp/auth.py` 是回 200 + tool error，需評估 discovery 觸發點要不要改成 401 + header，或雙軌）。
- 確保 Authentik 的 **authorization-server metadata**（`/.well-known/oauth-authorization-server`，RFC 8414）對 client 可達（Authentik 內建有，確認 nginx `/oauth/` 路由把它露出來）。
- **Pre-registered client_id 處理**：DCR 關閉（`planning/agent-interface/open-questions.md` 7c），所以只支援 DCR 的 client 需要手動填 pre-registered `client_id`；把 `claude-code` / `cursor` / claude.ai connector 等要支援的 client 在 Authentik + `app/auth/mcp_clients.py` 註冊好，並文件化「client 端要填哪個 client_id / redirect_uri」。
- 真人 delegated 走 **Auth Code + PKCE**（grant type 已在 auth Q2 模型內），login UI 重用既有 Authentik / Google SSO（T-056）。
- 端到端驗證：一個真 MCP client（先用 MCP Inspector 的 OAuth 模式，再試 Claude Desktop / claude.ai）連 `/mcp/` → 被導去登入 → 登入完自動拿 token → 成功呼叫 `character.list` / `character.create`。

**Not in scope（保留給其他單）：**
- **DCR（Dynamic Client Registration）**：已決定關閉（7c）。只支援 DCR 的 client 用 pre-registered client_id 變通；要不要為特定 client 開 DCR 是獨立 scope 決定。
- **M2M / headless agent**：T-080 已完成（client_credentials → 直接換 token，不需 PRM）。
- **`/v1/*` REST 的 scope 行為**：T-084 已 grandfather delegated token；本單只動 `/mcp/` 的 discovery，不改 REST grandfather。
- **Authentik scope emission 本身**：那是 S3.5-6 的事（本單的 hard dependency，不是本單範圍）。

---

## Planning refs（開工前必讀 + 需先補 plan）

- `planning/auth/open-questions.md` Q1 — Authentik 選型含 PRM / DCR-off / Client Credentials 能力
- `planning/auth/open-questions.md` Q2 — delegation（Auth Code + PKCE）+ M2M 並存
- `planning/agent-interface/open-questions.md` 7c — pre-registered allowlist（DCR 不開）
- `planning/agent-interface/scope.md` §1 完成條件 / §2 line 21（M2M headless 是 3.5c 的假設）
- `planning/devops/authentik-stack.md` — Authentik OAuth provider / nginx `/oauth/` 路由
- ⚠ 開工前需先跑 **plan phase**（agent-interface + auth 視角）：discovery 觸發點（200+tool-error vs 401+WWW-Authenticate）對既有 dual-stack bearer 行為的相容性、PRM `resource` identifier 要用哪個 URL、client_id/redirect_uri 約定。

---

## Acceptance criteria

- [x] `GET /.well-known/oauth-protected-resource` 回合法 RFC 9728 metadata（authorization_servers 指向 Authentik `character-foundry-mcp` issuer、scopes_supported 含 5 條 canonical、resource 是 `/mcp` 的 URL）—— `app/mcp/discovery.py` + `tests/mcp/test_discovery.py`
- [x] 無 token 打 `/mcp/` 觸發 discovery（401 + `WWW-Authenticate: Bearer resource_metadata="..."`），且**不破壞** T-080 對「帶了 token 但驗失敗」的 200 + tool-error 行為（plan Decision 2：只有完全沒帶 header 才 401）—— `app/mcp/auth.py` + `test_discovery.py` + `test_skeleton.py`（present-but-bad regressions 仍綠）
- [ ] **（Manual，待 operator）** 一個真 MCP client（MCP Inspector OAuth 模式起步）連 `/mcp/` → 自動導去 Authentik/Google 登入 → 取得 delegated token → 成功 `tools/list` + `character.list`。code + blueprint（`character-foundry-mcp` app）+ docs 已就緒；需起 docker stack + 真瀏覽器跑（沿用 CDP/manual OAuth pattern，同先前 OAuth ticket）
- [x] pre-registered client 的 client_id / redirect_uri 約定有文件化 —— `planning/agent-interface/mcp-oauth-discovery.md` §3 + `planning/devops/authentik-stack.md` §5.4
- [x] 既有 dual-stack bearer（手動帶 token）+ M2M client_credentials 路徑不回歸 —— 都帶 token，走 200 路徑不受 401-trigger 影響；skeleton happy paths + T-091 agent smoke（送 Bearer）皆未動
- [x] 測試綠（PRM endpoint 單元測試 + discovery 觸發的 transport 測試）；端到端真 client 那條標 Manual（見上）

---

## Files expected to touch（粗估，plan 後修正）

- `api/app/mcp/` — PRM endpoint + 401/WWW-Authenticate discovery（新檔或擴 `app/mcp/app.py` / `auth.py`）
- `api/app/auth/mcp_clients.py` — 補要支援的 delegated client 註冊
- `infra/authentik/blueprints/*` — 確認 AS metadata 露出 + client 設定
- `infra/nginx/nginx.conf` — `/.well-known/oauth-protected-resource` 路由（若需要）
- `planning/agent-interface/` + `planning/auth/` — plan phase 產出
- 測試：`api/tests/mcp/`

---

## OAuth scope required

`n/a`（不新增 `/v1/*` endpoint；本單動的是 `/mcp/` 的 OAuth discovery 機制，scope 仍是既有 5 條 canonical）

---

## MCP tool delta

`n/a`（不新增 / 不改 tool；改的是 MCP server 的 auth/discovery 層）

---

## Notes

- **為什麼 hard-depends S3.5-6**：`/mcp/` 走 strict `require_mcp_scopes`（讀 token 真實 scope，**故意不** grandfather —— 見 T-084 `_resolve_oauth` grandfather 只在 `/v1/*`）。所以真人登入後即使 discovery 全做對，只要 Authentik 沒把 `character:read` 等發進 delegated token，呼 `character.list` 仍 `AUTH_INSUFFICIENT_SCOPE`。S3.5-6 必須先解。
- **DCR-off 的後果**：純靠 DCR 自動註冊的 client 不會自動取得 client_id。要嘛該 client 支援手填 pre-registered client_id，要嘛將來為特定 client 評估開 DCR（另開單）。
- **discovery 觸發點的相容性風險**：T-080 刻意讓 auth 失敗回 `200 + CallToolResult.isError`（不是 HTTP 401），以符合 MCP error-vs-HTTP-status 約定。但 OAuth discovery 慣例靠 `401 + WWW-Authenticate`。兩者要怎麼共存（例如：只有「完全沒帶 Authorization header」時回 401+WWW-Authenticate 觸發 discovery，帶了但驗失敗仍回 200+tool-error）是 plan phase 要拍板的核心 trade-off。
- 來源：使用者 2026-05-21 在 T-084 MCP 手測時提出「期待 client 自己導去登入頁取 token」，確認要支援。
