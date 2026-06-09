# T-094: MCP delegated-client dev provisioning + delegated-token email scope

**Status:** IN PROGRESS
**Sprint:** Post-M3.5
**Est:** S
**Depends on:** T-089（done）, T-093（done）
**Related:** T-090（MCP host allowlist）

---

## Scope

把 T-089 一直標 Manual／沒人跑過的 AC #3（真人 MCP client 經 OAuth 自動登入連 `/mcp/`）**實際用 Claude Code 跑一遍**，並 land 這趟 dogfood 撈出的修正，讓**真人 delegated MCP 流程在 dev/prod 真的能用**。

開工方式即 manual E2E：Claude Code 當真人 MCP client → discovery → Google/Authentik 登入 → 跑 `character.create` → 全 27 個 tool 掃一遍。撞到 7 道牆，6 道是連線／provisioning，第 6 道是 **shipped code 的真 bug**（delegated token 沒帶 `email` claim，backend 無法把 token 映射到 `User`，丟 `AUTH_INVALID_TOKEN`）。

**In scope:**
- **`app/mcp/discovery.py`（真 bug fix）**：PRM `scopes_supported` 除了 5 個 app scope，**也宣告 `openid`/`email`/`profile`**。delegated client 才會 request identity scope → token 帶 `email` → `resolve_oauth_user_id` 能映射到 backend `User`。影響**所有** delegated MCP client（不只 dev）。CI 從沒抓到是因為 CI 只跑 M2M 路徑（用合成 service-account email，不需 `email` claim）。`character-foundry-mcp` 是 uncapped delegated client（`mcp_clients.ALLOWED_CLIENTS` `scopes=None`），所以多出的 identity scope 不會撞 `verify_oauth_token` 的 scope-cap 檢查。
- **`infra/authentik/blueprints/cf-mcp-dev.yaml`（new）**：dev 用 blueprint provision `character-foundry-mcp` provider/app + 5 條 canonical scope mapping（含 T-093 的 `return {"scope": ...}` expression）+ 綁既有 `cf-agent-default` group。補 `authentik-stack.md §5.4` 只寫 admin-UI、dev/prod 從沒真的建過的縫。只加 MCP 相關物件、不碰 SPA（`!Find` 引用既有 group，不動 membership）。
- **`docker-compose.override.yml`**：把 `cf-mcp-dev.yaml` 掛進 authentik-server + worker（worker 才是 apply blueprint 的）。
- **`api/tests/mcp/test_discovery.py`**：PRM `scopes_supported` 斷言更新成含 identity scopes，docstring 記錄 why。
- **`STATUS.md`**：backlog `S3.5-8`（dogfood 發現的 `character.list` `alias_count` list/detail 不一致）+ 本單追蹤。
- **`planning/devops/authentik-stack.md` §5.4/§5.9**：dev 改走 blueprint 的註記（避免 runbook 與新檔矛盾）。

**Not in scope**（保留給其他單）：
- motion 真生成 happy-path E2E —— 卡在 dev `VEO_API_KEY` 失效（401/403）；屬部署 credential，非 MCP。motion CRUD 的 happy-path（rename/delete 真 motion）連帶 deferred（需一個成功 motion）。
- prod blueprint apply（admin-UI / prod codify）—— dev 已覆蓋；prod mirror，§5.9 checklist 補一條。
- `alias_count` 修正本身（backlog `S3.5-8`）。
- MCP CORS（`S3.5-7`）。

---

## Planning refs（開工前必讀）

- `planning/devops/authentik-stack.md` §5.4 — character-foundry-mcp 是第 6 個 app，dev/prod 本來寫 admin-UI
- `planning/agent-interface/mcp-oauth-discovery.md` — T-089 discovery 設計全貌
- `planning/backend/oauth-mcp-integration.md` — scope / client allowlist

---

## Acceptance criteria

- [x] PRM `scopes_supported` 含 `openid`/`email`/`profile`；`test_discovery.py` 更新 + 綠
- [x] `cf-mcp-dev.yaml` apply 到 dev Authentik：`BlueprintInstance.status == successful`、`character-foundry-mcp` OIDC config 回 200、provider 掛上 5 條 cf-scope mapping
- [x] **真人 delegated 流程端到端**（Claude Code → `/mcp/` OAuth → `character.create`）建出 character + base，owner = 真人帳號（2026-06-09 驗證）
- [x] 全 27 個 MCP tool invoke 過、行為正確（讀 / CRUD / 錯誤路徑）；motion happy-path defer（Veo key）
- [x] `ruff check` / `ruff format --check` / `mypy app/`（146 檔）/ `pytest tests/mcp/test_discovery.py` / 3 條 MCP guardrail / arch `test_oauth_scope_source_is_centralized` 全綠
- [ ] CI 綠

---

## Files expected to touch

- `api/app/mcp/discovery.py` (edit)
- `infra/authentik/blueprints/cf-mcp-dev.yaml` (new)
- `docker-compose.override.yml` (edit)
- `api/tests/mcp/test_discovery.py` (edit)
- `STATUS.md` (edit)
- `planning/devops/authentik-stack.md` (edit)
- `tickets/T-094-*.md` (new)

非 code、屬 dev runtime/local（不在 diff，文件化於 runbook + memory）：`.env` `MCP_ALLOWED_HOSTS` 補 bare `localhost`（T-090 default 被顯式 env 覆蓋的縫）、改 nginx.conf 後要 `nginx -s reload`、`claude mcp add --client-id`（Authentik 關 DCR）。

---

## OAuth scope required

`n/a` —— 沒新增/改動 `app/api/routes/` endpoint。`discovery.py` 是 unauthenticated 的 RFC 9728 metadata，不掛 `require_scope`。

---

## MCP tool delta

`n/a` —— 沒動 `app/mcp/tools/` registry。本單是 agent surface 的**端到端驗證 + 連線修復**，不新增/改 tool。
