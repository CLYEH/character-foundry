# T-092: MCP M2M service-account identity

**Status:** IN_PROGRESS
**Sprint:** 3.5c（unblocks T-091）
**Est:** S–M（auth 層 + identity provisioning + 單元測試；security-sensitive）
**Depends on:** T-080（MCP dual-stack auth）、T-054（OAuth verifier）、T-071（OAuth auto-provisioning pattern 範本）
**Related:** T-091（Agent E2E smoke —— 本單 land 後才能真正端到端跑 create-flow），T-084/85/86（packaged tools，現行對 M2M 唯讀）

---

## Scope

讓 **headless M2M（client_credentials）agent 成為 first-class resource owner**：sanctioned M2M client 的 token 在 `/mcp/*` 解析到一個 provisioned backend **service-account User**，agent 因此能跑 create-flow（建 character / alias / motion）並擁有它建的 resource —— 對齊業界標準的 M2M service-principal 模式（client_credentials 的 `sub` 即 service identity，service 擁有它建立的東西）。

**Root cause（T-091 開工 reveal）**：現行 `resolve_mcp_token`（`app/mcp/auth.py:266`）對 M2M token 一律回 `user_id=None`；而 `character.create` / `alias.add` / `motion.generate` / `task.get` 都呼 `require_user_context()`，對 `user_id=None` 回 `AUTH_USER_CONTEXT_REQUIRED`（`auth.py:180`）。T-084/85/86 刻意讓 M2M 對 user-owned resource 唯讀。結果 plan（scope.md §2.1「Client Credentials 給 headless agent」、STATUS「3.5c 走 M2M」）與 shipped code 衝突 —— M2M 連 character 都建不了。本單補上「M2M agent 有 service identity」這塊，是 plan 一直假設、但 Wave B 沒做的能力。

**In scope:**
- **Sanctioned set**：`app/auth/mcp_clients.py` 加 `M2M_SERVICE_ACCOUNT_CLIENTS`（顯式 frozenset，初始 `{"cf-test-agent"}`）。只有列在其中的 M2M client 才取得 service identity；其餘 M2M client 維持 `user_id=None`（唯讀，現狀不變）—— fail-closed、不讓新 M2M client 默默拿到 resource ownership。
- **Provisioning**：`app/auth/provisioning.py` 加 `auto_provision_m2m_service_user(client_id) -> User`（mirror `auto_provision_oauth_user`：default team、隨機不可登入 password hash、synthetic email keyed by client_id、name 標 service agent；IntegrityError race → re-select）。
- **Resolution**：`app/auth/user_resolution.py` 加 `resolve_m2m_service_user_id(client_id, db) -> uuid.UUID`（lower(email) 查 → 無則 provision）。
- **Wire-in**：`app/mcp/auth.py` 的 M2M branch —— `client_id ∈ M2M_SERVICE_ACCOUNT_CLIENTS` 時解析 `user_id` 到 service user；否則維持 `None`。**`is_m2m` 保持 `True`**（`/v1/*` 仍由 is_m2m flag reject `auth_m2m_wrong_surface`，service identity 只在 `/mcp/*` 生效）。
- 單元測試 + planning 同步（authentik-stack.md / scope.md / DECISIONS 註）。

**Not in scope（保留給其他單）：**
- **Agent E2E smoke 本身 + Authentik cf-test-agent provider blueprint + CI** → T-091（本單的端到端驗證在那邊）。
- **真人 delegated（Auth Code + PKCE）的 user 解析** → 已由 T-071 + T-084 grandfather 處理，本單不動。
- **Per-agent team / 跨 team service account / quota 歸屬** → Phase 1 single team（B5），service user 一律進 default team；多 team 是 Phase 2。
- **DB schema 改動**：刻意用 synthetic email 當 key（無 migration），不加 `service_client_id` 欄位（避免 schema migration 的 2-人 review + db-optimizer 成本，且 email-as-key 是業界 `<client_id>@clients` 慣例）。

---

## Planning refs（開工前必讀）

- `planning/auth/open-questions.md` Q2（agent grants：client credentials M2M vs delegation）
- `planning/agent-interface/scope.md` §2.1（Client Credentials 給 headless agent）+ §1（完成條件）
- `app/auth/provisioning.py`（T-071 auto-provision 範本：default team / random hash / race handling）
- `app/auth/user_resolution.py`（resolve_oauth_user_id 範式）
- `app/mcp/auth.py`（resolve_mcp_token M2M branch + require_user_context + MCPAuthContext docstring）
- `DECISIONS.md` §6 B5（single team）

---

## Acceptance criteria

- [ ] Sanctioned M2M client（`cf-test-agent`）的 verified token → `resolve_mcp_token` 回 `MCPAuthContext(user_id=<service user>, is_m2m=True, client_id="cf-test-agent", scopes=...)`；`require_user_context` 通過。
- [ ] 第一次解析 auto-provision 一個 service-account User（default team、不可登入 password）；第二次解析 reuse 同一個 user（idempotent）；併發 race（IntegrityError）re-select 不 500。
- [ ] **Unsanctioned** M2M client（不在 set）→ `user_id=None` 維持（唯讀 M2M 行為不回歸）。
- [ ] `is_m2m` 仍為 `True`：`/v1/*` 對 service-account token 仍回 `auth_m2m_wrong_surface`（service identity 不外洩到 human surface）。
- [ ] service-account User 無法被當人用：synthetic email + 隨機 hash → 不能走 `/v1/auth/login` 拿 JWT（密碼無人知）。
- [ ] 測試綠：`pytest tests/auth tests/mcp -q`（含新 `tests/auth/test_m2m_service_account.py`）；`ruff check . && ruff format --check . && mypy app/`；MCP guardrails（`bash scripts/lint_mcp.sh`）不回歸。

---

## Files expected to touch

- `api/app/auth/mcp_clients.py`（edit）— `M2M_SERVICE_ACCOUNT_CLIENTS` frozenset
- `api/app/auth/provisioning.py`（edit）— `auto_provision_m2m_service_user`
- `api/app/auth/user_resolution.py`（edit）— `resolve_m2m_service_user_id`
- `api/app/mcp/auth.py`（edit）— M2M branch wire-in
- `api/tests/auth/test_m2m_service_account.py`（new）— 單元測試
- `planning/devops/authentik-stack.md` / `planning/agent-interface/scope.md`（edit）— service-account identity 模型落地
- `STATUS.md`（edit）— 3.5c 區塊加 T-092；known-risks 若有
- `tickets/T-092-*.md` → `tickets/DONE/`

---

## OAuth scope required（後端 endpoint 必填；frontend / docs / infra 票寫 `n/a`）

`n/a` —— 不新增 / 不改 REST endpoint。本單改的是 `/mcp/*` 的 M2M token → user identity 解析；scope 仍是既有 5 條 canonical。

---

## MCP tool delta（agent surface 影響；無影響寫 `n/a`）

`n/a` —— 不新增 / 不改 tool。改的是 MCP auth 層：sanctioned M2M token 取得 service-account `user_id`，使既有 user-scoped packaged tool 可被 headless agent 呼叫（行為從「M2M 撞 AUTH_USER_CONTEXT_REQUIRED」變「M2M 以 service identity 執行」）。

---

## Notes

- **為什麼是 service-account User 而非新 principal type**：characters.owner_id 是 `users.id` FK，整個 ownership / team visibility / permission（`assert_can_modify_character`）都建在 User 上。reuse User row 是最小改動、零 migration、且 service-account-as-user 是 Auth0 `<client_id>@clients` / GitHub App bot user 等業界慣例。
- **Synthetic email key**：`agent+{client_id}@example.com`（RFC 2606 reserved domain，EmailStr-valid 避免任何 owner DTO 序列化 422；`+client_id` 當穩定 key）。`cli.py` 註明 `.local` 被 EmailStr 拒，故沿用 `example.com`。
- **安全邊界（security review 重點）**：(1) service identity 只在 `/mcp/*` 生效，`is_m2m=True` 讓 `/v1/*` 仍 reject —— headless 身分不外洩到 human REST surface；(2) gate 是顯式 `M2M_SERVICE_ACCOUNT_CLIENTS` set（fail-closed，新 M2M client 不默默拿 ownership）；(3) client_id 來自 cryptographically-verified token（已過簽章 + ALLOWED_CLIENTS + scope cap）；(4) service user 密碼是隨機不可登入 hash，無 JWT-login 旁路；(5) 進 default team，與既有 single-team 模型一致。
- **⚠ Team-read blast radius（review reveal，已文件化於 `M2M_SERVICE_ACCOUNT_CLIENTS` docstring）**：service account 進 `default` team，而 `list_characters(owner_id=None)` 是 **team-scoped 非 owner-scoped**，故持 `character:read` 的 sanctioned agent 可讀**整個 team 所有真人的 character**，不只它自己建的。這是既有 single-team visibility 模型的固有性質、非本單引入，但加 client 進 allowlist = 授予 team-wide read（不只 self-owned write）。per-agent team isolation 是 Phase 2。兩位 reviewer（engineering + security）皆無 blocker；此條為操作邊界提醒。
- **與 T-071 的對稱**：T-071 是 delegated first-login auto-provision（email-domain gate）；本單是 M2M first-call auto-provision（client_id allowlist gate）。同一條 provisioning 模組、同樣 default team + 隨機 hash + race handling。
- **`MCPAuthContext` docstring 要更新**：目前寫「user_id is None for M2M」——改成「None for M2M unless the client is a sanctioned service account (T-092)」。
- **此單觸發 security-engineer subagent**（ticket 命中 OAuth / scope / token / client_secret 關鍵字）——push 前跑。
