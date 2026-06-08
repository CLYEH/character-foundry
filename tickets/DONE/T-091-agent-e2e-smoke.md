# T-091: Agent E2E smoke — 外部 M2M agent 經 OAuth + MCP 跑完 M3 全流程

**Status:** DONE（PR #124，2026-06-08；M3.5 ship gate 達成）
**Sprint:** 3.5c（Agent E2E smoke）
**Est:** M（原 scope.md §5.3 估 0.5 週，但 milestone-true 版本要 seed 真的 Authentik M2M provider —— e2e bootstrap 當初刻意 defer 了 cf-test-agent，補上是本單主要工作量，實際 > 0.5 週）
**Depends on:** T-092（M2M service-account identity；**hard blocker** —— 沒有它 M2M token `user_id=None`，create-flow 全部撞 `AUTH_USER_CONTEXT_REQUIRED`）、T-080 / T-081 / T-082 / T-084 / T-085 / T-086 / T-088（全 DONE）
**Related:** T-089（真人 delegated discovery / auto-login —— 本單只走 M2M client_credentials，不依賴 T-089；T-089 反過來 hard-depends S3.5-6）

---

## Scope

用一個**真正外部的 M2M agent**，只憑 OAuth 設定 + MCP tool schema（不看 REST 文件），經 client_credentials 拿 token → 連 `/mcp/` → 跑完 M3 範圍全流程（建 character → 確立 base → 加 alias → 生 motion），作為 **M3.5 milestone 的 ship-gate smoke**。跑綠 = `scope.md §1 完成條件`達成、M3.5 可勾。

**In scope:**
- **Authentik 端 seed cf-test-agent M2M provider**：新 blueprint，建 `cf-test-agent` OAuth2 provider（confidential，client_credentials）+ internal service account user + 固定 CI-only token（app_password intent）+ `cf-test-agent-full` group + application + group→app policy binding + 5 條 canonical scope property mapping（`!Find` 既有的，不重定義）。對齊 `authentik-stack.md` §5.4/§5.5 當初為 T-053 規劃但 e2e 沒 seed 的部分。
- **外部 agent smoke harness**（`api/scripts/agent_e2e_smoke.py`，standalone）：**只 import `mcp` client SDK + `httpx` + stdlib，不 import 任何 `app.*`**。流程：(1) `POST /oauth/application/o/token/` client_credentials 拿 access token；(2) 開 streamable-HTTP MCP session 連 `http://localhost/mcp/`；(3) `tools/list` 確認看得到 packaged tools；(4) `character.create`（template mode）→ 解出 base；(5) `alias.add`；(6) `motion.generate` → `task.get` 輪詢到 terminal；逐步斷言成功，非零 exit 代表 gate 紅。
- **CI wiring**：加進現有 `pr.yml` e2e job 當一步（每 PR 跑），在 stack up + migrations + seed 之後、Playwright 之前/之後。stack 已起著，增量成本低。
- **Planning 同步**：`scope.md` §5.2.1 標 3.5c done；`authentik-stack.md` §5.4/§5.5/§5.6.2 補「e2e 用 blueprint seed cf-test-agent + CI-only token 出處」；operator pass（見 Notes）。

**Not in scope（保留給其他單）：**
- **真人 delegated client OAuth discovery / auto-login**（PRM / RFC 9728 / `WWW-Authenticate`）→ T-089。
- **真 AI 品質驗證**：smoke 跑 `AI_STUB_MODE=true`（stub Veo / gpt-image-2），只驗 agent-native **合約**（OAuth → MCP → packaged tool → poll → 成品 shape），不驗生成品質（那是 M3 範圍、已驗）。
- **ZIP / Copy / Usage 的 agent 流程** → M4（per scope.md §1 註）。
- **prod 端 cf-test-agent provider codify**：prod 仍走 admin-UI runbook（mirror cf-google-init.yaml prod gap），M3.5 ship-prep 一起處理。

---

## Planning refs（開工前必讀）

- `planning/agent-interface/scope.md` §1（M3.5 完成條件）+ §5.3（3.5c 定義）+ §2.2（async-submit + poll-by-task-id）
- `planning/devops/authentik-stack.md` §5.4（cf-test-agent provider 規格）+ §5.5（group / policy binding）+ §5.6.2（client_credentials curl 請求 shape，token endpoint 路徑）
- `planning/auth/open-questions.md` Q1/Q2（Authentik + Client Credentials M2M）
- `planning/agent-interface/endpoint-mcp-mapping.md` §3（packaged tool bundles / 輸入）
- `infra/authentik/blueprints/cf-e2e-bootstrap.yaml`（既有 scope mapping / provider / group / policy binding 範式 + 2024.12 silent-failure 陷阱註解）
- `.github/workflows/pr.yml` e2e job（stack-up / health-gate / seed 流程）

---

## Acceptance criteria

> **驗收結果（PR #124 CI 綠；含 integration gate 抓到的 2 個修正）。** 落地用 Authentik **Option-3 M2M**（provider client_secret → 自動 service account），故**未** seed 手動 SA / token / `cf-test-agent-full` group / policy binding——原 AC 假設的「手動 SA + group + binding」改成 Option-3，下面已對齊。

- [x] 新 blueprint apply 後 `BlueprintInstance.status == successful`（CI run #1 因漏 `redirect_uris` → status=error；補 placeholder 後綠）；`cf-test-agent` provider + application 實際存在（Option-3：無手動 SA / token / group / binding——auto-SA 由 client_credentials grant 自建）。
- [~] `POST .../o/token/` client_credentials 回 200 + JWT，**`aud`/`azp` == `cf-test-agent` ✅、簽章/`iss` ✅**；**但 `scope` claim 是空的 ❌**（Authentik 2024.12 不把 5 條 custom app scope 發進 CC token——即 S3.5-6，本單端到端確認也打到 M2M）。**workaround**：`resolve_mcp_token` 對 M2M 空 scope claim fallback 到 allowlist cap（security-reviewed，PR #124 `4a41796`）。所以「agent 能以 5 scope 呼叫 tool」的**意圖達成**，但「token scope claim 含 5 條」的**字面未達成**（記入 STATUS S3.5-6，待真修 emission 時連 fallback 一起退場）。
- [x] smoke harness 連 `http://localhost/mcp/` `tools/list` 看得到 4 個 tool；逐一跑完 character→base→alias→motion 全綠（stub AI）——CI e2e smoke step 證實。
- [x] CI e2e job 新增的 smoke step 在 PR #124 跑綠（gate 證據）。
- [x] 既有 e2e（Playwright specs）+ backend / mcp 測試不回歸（PR #124 4 個 check 全 SUCCESS）。
- [x] harness 不 import `app.*`（CI + 本機 AST grep 驗：只有 `mcp` / `httpx` / stdlib）。

---

## Files expected to touch

- `infra/authentik/blueprints/cf-mcp-agent.yaml`（new）— cf-test-agent M2M provider + SA + token + group + app + binding
- `api/scripts/agent_e2e_smoke.py`（new）— 外部 agent smoke harness
- `.github/workflows/pr.yml`（edit）— e2e job 加 smoke step（+ 必要的 setup-python / pip install mcp httpx）
- `docker-compose.test.yml`（edit, 視需要）— 若 blueprint 走 `!Env` 讀 token 才需；預設走 blueprint 內 CI-only 字面值則不必動
- `planning/agent-interface/scope.md`（edit）— §5.2.1 標 3.5c done
- `planning/devops/authentik-stack.md`（edit）— §5.4/§5.5/§5.6.2 補 e2e blueprint seed 出處 + operator pass
- `STATUS.md`（edit）— 3.5c 收尾 + M3.5 milestone
- `tickets/T-091-*.md` → `tickets/DONE/`

---

## OAuth scope required（後端 endpoint 必填；frontend / docs / infra 票寫 `n/a`）

`n/a` —— 不新增 / 不改 REST endpoint。smoke 用既有 5 條 canonical scope（`character:read/write` / `task:read/cancel` / `usage:read`），cf-test-agent 在 `app/auth/mcp_clients.py` 已 override 為全 5 條。

---

## MCP tool delta（agent surface 影響；無影響寫 `n/a`）

`n/a` —— 不新增 / 不改 tool。本單是對既有 packaged tool（T-084/85/86）+ MCP transport（T-080/82）跑端到端驗證。

---

## Notes

- **⚠ Hard blocker reveal（2026-06-08，開工時讀 auth 層發現）**：M2M client_credentials token 的 `user_id=None`（`app/mcp/auth.py:266`），而 create-flow 的每個 tool（`character.create` / `alias.add` / `motion.generate` / `task.get`）都呼 `require_user_context()`，對 `user_id=None` 回 `AUTH_USER_CONTEXT_REQUIRED`（`auth.py:180`）。T-084/85/86 刻意讓 M2M 對 user-owned resource 唯讀。所以 plan 說的「3.5c 走 headless M2M create-flow」與 shipped code 衝突。**解法（使用者 2026-06-08：「最漂亮 + 最標準」= 業界 M2M service-principal owns resources 模式）= T-092**：讓 /mcp M2M token 解析到 provisioned backend service-account User，agent 擁有它建的 resource。T-091 等 T-092 land 後才能真正端到端跑通。
- **決策出處（使用者 2026-06-08 拍板）**：(1) **Full real OAuth path** —— seed 真 Authentik M2M provider，smoke 用真 client_credentials token，不走 backend-JWT 捷徑（捷徑會跳過 milestone 的 OAuth-login 半邊）。(2) **CI 走每 PR 的 e2e job step**，不走獨立 workflow_dispatch。
- **AI stub**：smoke 跑在既有 e2e stack（`AI_STUB_MODE=true`），`StubAIClient` / `VeoStub` 回 fixture，全程不碰真 provider quota（`api/app/ai/factory.py` + `config.py`）。
- **為什麼 harness 跑在 runner 打 `http://localhost` 而非 in-container 打 `http://nginx`**：MCP host allowlist（T-090 / `_DEFAULT_ALLOWED_HOSTS`）只收 loopback；in-container 打 `http://nginx/mcp/` 送 `Host: nginx` 會 421。runner 打 `http://localhost`（Host: localhost 已 allowlist）跟 Playwright e2e 同源、最貼近真實 external agent。
- **為什麼放 `api/scripts/` 不放 `api/tests/`**：`api/tests/` 會被 backend-lint-test 的 pytest 收集 → 沒 live stack 會 fail。`scripts/` 不在 pytest `testpaths`（對齊既有 `check_*.py` 慣例），CI 用 `python api/scripts/agent_e2e_smoke.py` 顯式跑。Dockerfile 沒 COPY `scripts/`，但 harness 跑在 runner 不在 image 內，無影響。
- **Authentik client_credentials 機制（authentik-stack.md §5.6.2）**：`client_secret` 帶的是 **service account 的 token（app_password intent）**，不是 provider 自己的 secret；Authentik 靠這個 token 解出 acting service-account user，`client_id` 選 provider（決定 scope / property mappings）。blueprint 要建：`authentik_core.user`（type service_account）+ `authentik_core.token`（intent app_password, 固定 `key`）+ provider（confidential）+ application + `cf-test-agent-full` group（含 SA user）+ group→app policy binding。
- **⚠ 2024.12 blueprint silent-failure 陷阱**（memory `authentik_blueprint_2024_12_gotchas`）：apply Task 回 SUCCESS 但 entry schema 不符時 BlueprintInstance.status=error。逐欄對照既有 cf-e2e-bootstrap.yaml 已驗證的形式（provider 必填 `invalidation_flow` / `authorization_flow` / `signing_key`；group membership 用 `users` 不用 `users_obj`；policy binding 的 `group` 放 attrs 不放 identifiers；redirect_uris 對 CC provider 可省）。token / service_account 模型欄位先對 Authentik 2024.12 docs 驗一次再寫。
- **CI-only token 出處**：seeded SA token 用低熵 throwaway 字面值（`cf-e2e-test-agent-secret-not-for-prod` 類，避免 gitleaks 熵偵測），與 `AUTHENTIK_BOOTSTRAP_TOKEN` 同 pattern。**不進 `.env.example`**（per authentik-stack.md §5.4 備註），prod 真 secret 走 1Password。
- **Operator persona pass（planning/CLAUDE.md §1）**：本單動到一條 config surface（M2M provider）。(1) **Admin/config 路徑**：cf-test-agent 改 scope / rotate secret → Authentik admin UI `/oauth/if/admin/` → Providers/Applications/Tokens，或改 blueprint re-apply。(2) **Break-glass**：CI smoke 紅但疑 Authentik 設定漂 → 用 bootstrap token 打 `/oauth/api/v3/` 查 provider/token/binding 實況（pr.yml 既有 dump pattern）。(3) **拓樸**：cf-test-agent 入口與真人 SPA 入口分屬不同 application，不交疊。落地寫進 authentik-stack.md。
- **起手驗證順序**：先在本機 `docker compose -f docker-compose.yml -f docker-compose.test.yml up -d --build` 起 stack → 確認新 blueprint apply successful → curl client_credentials 拿到 token → 跑 harness → 再 wire CI。
