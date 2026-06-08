# T-093: Authentik delegated-token scope emission（resolve backlog S3.5-6）

**Status:** TODO
**Sprint:** post-M3.5（unblocks T-089）
**Est:** M（含 live-stack 診斷；fix size 在 Phase 1 decode token 前未知）
**Depends on:** none（這張單就是要解掉別人都在 workaround 的 root cause）
**Related:** T-089（hard-depends 本單）、T-084（`/v1/*` grandfather workaround）、T-091（M2M cap-fallback workaround）、T-053（Authentik client 註冊）

---

## Scope

把 backlog **S3.5-6** 從「三張單各自 workaround」升級成「真正修好」：讓 Authentik 2024.12
在 OAuth flow 把 5 條 canonical app scope（`character:read/write`、`task:read/cancel`、
`usage:read`）**真的發進 token 的 `scope` claim**——delegated（真人 SPA 登入）與 M2M
（client_credentials）兩種都要。

**為什麼是 root-cause 而非 config gap：** e2e blueprint **已經**定義 5 條 ScopeMapping
（`cf-e2e-bootstrap.yaml:60-104`）**且已 attach** 到 SPA provider 的 `property_mappings`
（`:236-245`）、M2M provider 也 attach 了（`:335-340`），SPA 也**確實 request** 那 5 條
（`web/src/config.ts:14`）。但發出來的 token `scope` claim **兩種都是空的**——T-084 看到
delegated token 在 `/v1/*` 被 403、T-091 看到 M2M token scope claim 空。三張單都**繞過**
（`/v1/*` grandfather、M2M cap-fallback），**沒有人 decode 過 live token 去診斷為什麼**。

**In scope:**
- **Phase 1（gating）診斷**：跑 live e2e stack，驗 `BlueprintInstance.status`、`ak shell`
  確認 provider 的 `property_mappings` 真的進 DB；decode 真 M2M + delegated token 看 `scope`
  claim；把 root cause 釘到具體原因（blueprint silent-failure / authorization 沒授 / service-
  account authz / 2024.12 emission quirk）。**先寫出帶 token 證據的 root cause。**
- 依 Phase 1 結果修 emission：`cf-e2e-bootstrap.yaml`（e2e）、`cf-google-init.yaml`（dev 目前
  連 scope 定義都沒有）、`authentik-stack.md` §5（prod runbook）。
- Backend 清理：M2M emission 真修好後**移除 cap-fallback**（`app/mcp/auth.py:300`，security 🟡）；
  decode 確認 delegated token 帶 5 scope 後，`/mcp/` strict enforcement 即可信任真 claim。
- 端到端驗證：decode delegated token → `scope` 含 5 條；`/mcp/` `character.list` 用該 token 成功；
  insufficient-scope token 被 `AUTH_INSUFFICIENT_SCOPE` 擋。

**Not in scope（保留給其他單）：**
- **T-089 的 PRM / discovery / WWW-Authenticate**：本單只修 scope emission（T-089 的 hard-dep），
  不做 discovery 機制。
- **`/v1/*` grandfather 的移除**：建議**保留**當 deliberate human-surface design（理由見 Notes），
  只把註解從「workaround」改成「by design」；不改行為。若要真的在 `/v1/*` 對 delegated 做
  per-scope 限制是另一張單。

---

## Planning refs（開工前必讀）

- `planning/devops/authentik-stack.md` §5 — Authentik OAuth provider / scope mapping / consent flow runbook
- `planning/auth/open-questions.md` Q1（PRM/DCR/CC 能力）、Q2（delegation + M2M 並存）、Q3（canonical scope 字串權威來源）
- `planning/agent-interface/oauth-mcp-integration.md` §3 §5 — scope / bundle / consent 設計
- STATUS.md backlog **S3.5-6**（本單要 close 的條目）+ T-091 條目（M2M cap-fallback 細節 + 「真修好要一併移除」的註記）

---

## Acceptance criteria

- [ ] **Root cause 寫清楚**：帶 decoded-token 證據（M2M + delegated 各一），釘到具體原因，記進 ticket Notes / STATUS
- [ ] `BlueprintInstance.status == successful` 且 `ak shell` 驗 SPA provider 的 `property_mappings` 真含 5 條 scopemapping
- [ ] Decode 一個**新發的 delegated** access token → `scope` claim 含 `character:read character:write task:read task:cancel usage:read`
- [ ] `/mcp/` 用該 delegated token 呼 `character.list` 成功（不靠 grandfather/fallback）；故意給缺 scope 的 token → `AUTH_INSUFFICIENT_SCOPE`
- [ ] M2M cap-fallback（`app/mcp/auth.py:300`）移除後 M2M 路徑仍綠（真 emission 帶 scope，不再靠 fallback）；若 M2M 無法本輪修好，fallback 收窄到 known-broken client 並寫明理由
- [ ] dev `cf-google-init.yaml` 補上 5 條 ScopeMapping（或文件化 admin-UI 步驟）；prod runbook §5 同步
- [ ] 測試綠：`pytest api/tests/auth api/tests/mcp`、3 條 MCP guardrail、full backend suite；e2e OAuth login smoke 不回歸

---

## Files expected to touch

- `tickets/T-093-authentik-delegated-scope-emission.md` (new)
- `infra/authentik/blueprints/cf-e2e-bootstrap.yaml` (edit — 依 Phase 1)
- `infra/authentik/blueprints/cf-google-init.yaml` (edit — dev 補 scope 定義)
- `api/app/mcp/auth.py` (edit — 移除/收窄 M2M cap-fallback `:300`)
- `api/app/api/deps.py` (edit — `/v1/*` grandfather 註解改 by-design `:115`，行為不變)
- `planning/devops/authentik-stack.md` (edit — §5 prod runbook)
- `api/tests/auth/test_m2m_service_account.py` (edit — fallback 移除後的預期)
- `api/tests/mcp/` (new/edit — delegated 真 scope enforcement 測試)
- 可能 `web/tests/e2e/oauth-login.spec.ts`（delegated token scope 斷言）

---

## OAuth scope required

`n/a`（不新增 / 不改 `/v1/*` endpoint；本單修的是 Authentik 把既有 5 條 canonical scope 發進 token 的機制）

---

## MCP tool delta

`n/a`（不新增 / 不改 tool；改的是 token 的 scope emission + auth resolution 清理）

---

## Notes

- **為什麼 backend 幾乎不用改**：delegated `/mcp/` 路徑早就直接讀 `claims.scopes`
  （`app/mcp/auth.py:330`，無 fallback）。Authentik 一旦把 scope 發進 token，現行碼就會正確授予——
  唯一要動的是把 M2M 的 cap-fallback（`:300`）退場。
- **`/v1/*` grandfather 建議保留**：SPA request 的就是全部 5 條（所以 grandfather ≡ real-claims
  對 SPA 無差），且真 MCP client 打 `/mcp/` 不打 `/v1/*`；改成 real-claims 沒好處、卻會讓每個真人
  REST caller 暴露在 regression 風險下。把 `deps.py` 註解從「workaround / S3.5-6」改寫成 deliberate
  human-surface design 即可。
- **2024.12 silent-failure 前科**：blueprint apply 回 Task SUCCESS 但 `BlueprintInstance.status=error`
  的 class（`users/users_obj`、policybinding group placement、`invalidation_flow` 必填、`redirect_uris`
  dict 格式都踩過）。Phase 1 第一件事就是排除「property_mappings 其實沒進 DB」。
- **診斷以 e2e stack 為主**：e2e 全 blueprint-driven、deterministic；dev 要真 Google 登入較難自動化。
- **fix size 未知**：Phase 1 decode token 前無法精確估。若診斷後發現是 L-sized（例如要自訂 scope-mapping
  expression / Authentik 版本升級），先 surface、把 fix 拆 follow-up，本單收斂成「診斷 + e2e 修好」。
- 來源：使用者 2026-06-08「start T-089」→ 勘查發現 T-089 卡 S3.5-6 是未診斷的真 bug → 拍板先做本單。
