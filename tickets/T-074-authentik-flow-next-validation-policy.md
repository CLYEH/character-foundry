# T-074: Authentik flow-executor `next` open-redirect — validate `next` is same-origin on SPA-launched flows

**Status:** TODO
**Sprint:** Backlog（post-3.5a；security hardening — T-073 security review defer）
**Est:** S
**Depends on:** none（T-073 已 land，本單獨立）
**Related:** T-073（揭露這條；`cf-google-init` flow 是要綁 policy 的對象之一）

---

## Scope

把 Authentik flow-executor 的 `?query=next=` 從「任意 URL 都 honor」收緊成「只 honor same-origin / relative」，至少綁在 SPA 實際會 launch 的 flow（`cf-google-init` + provider authentication flow）上。

### 背景（T-073 security review 2026-05-14 揭露）

Authentik 2024.12.5 的 `FlowExecutorView._flow_done()` 在 plan context 有 `PLAN_CONTEXT_REDIRECT` 時**直接 `redirect()` 過去、不做 `is_url_absolute` 驗證**（程式碼裡有明確 comment 說「context redirect 只會被 expression policy 或 authentik 自己設定，所以不檢查」）。但 `SourceFlowManager._prepare_flow` 會把 `SESSION_KEY_GET[next]` 塞進 `PLAN_CONTEXT_REDIRECT` —— 而 `SESSION_KEY_GET` 是 flow-executor 的 `dispatch()` 從 `?query=` 無條件寫入的。

→ 結果：`/oauth/if/flow/<any-flow>/?query=next=https://evil.com` 在使用者走完登入後，會被 redirect 到 `evil.com`（純 open-redirect / phishing landing，evil.com **拿不到** code 或 token，但仍是 open-redirect 漏洞類）。

**這是 Authentik core 的既有行為，存在於每一條 flow-executor URL（`default-authentication-flow` 等都一樣），不是 T-073 引入或加劇的** —— T-073 只是多加了一個 flow-executor 進入點。T-073 security review 判定「正確地不在 T-073 scope」，但屬於 deployment 內的 standing risk，開本單追蹤。

**In scope:**
- 評估 Authentik 機制：expression policy 綁在 flow 上、在 plan 階段檢查 `context['flows/get']['next']`（或 `request`）是否 same-origin / relative，不是就拒絕或改寫成安全 default
- 至少綁 `cf-google-init`；評估是否該綁所有 SPA 會碰的 authentication flow
- Codify 進 blueprint（`cf-google-init.yaml` 或新檔），讓它跟 flow 一起 survive DB reset
- 確認不會誤殺正常流程：SPA 送的 `next` 永遠是 relative 的 `/oauth/application/o/authorize/?...`（config-derived），same-origin policy 不該擋它

**Not in scope**（保留給其他單）：
- 改 Authentik core 程式碼（不可行，container image）
- SPA 端的 `buildSourceInitUrl` —— 它已經只餵 config-derived `authorizeUrl`，trust boundary 在 docstring 標清楚了（T-073）

---

## Planning refs（開工前必讀）

- `planning/devops/authentik-stack.md` §5.2.1 — `cf-google-init` flow + `next`-propagation 機制（T-073 落地）
- `infra/authentik/blueprints/cf-google-init.yaml` — 要綁 policy 的 flow
- memory `authentik_blueprint_2024_12_gotchas` — blueprint policybinding 的已知陷阱（`policybinding` model label、`group`/`target` 該放 identifiers 還是 attrs）

---

## Acceptance criteria

- [ ] `/oauth/if/flow/cf-google-init/?query=next=https://evil.com` 走完登入後**不會** redirect 到 `evil.com`（被擋或改寫成安全 default）
- [ ] 正常 SPA 登入（`next` 是 relative authorize URL）仍正常走到 Dashboard，policy 不誤殺
- [ ] policy + binding codify 在 blueprint，DB reset 後仍在
- [ ] 測試都綠（既有 `web/tests/e2e/oauth-login.spec.ts` 維持綠）

---

## Files expected to touch

- `infra/authentik/blueprints/cf-google-init.yaml`（edit — 加 expression policy + policybinding）或新 blueprint 檔
- `planning/devops/authentik-stack.md` §5.2.1（edit — 補 policy 說明）
- `STATUS.md` (edit)

> **E2E coverage gate（CONTRIBUTING §3.5）：** 預期 **N/A** —— 改的是 Authentik flow policy / blueprint，不是 React Router route / SPA code。既有 e2e 須維持綠。

---

## OAuth scope required（後端 endpoint 必填；frontend / docs / infra 票寫 `n/a`）

`n/a`（Authentik flow policy；不碰 backend endpoint）

---

## MCP tool delta（agent surface 影響；無影響寫 `n/a`）

`n/a`

---

## Notes

### 怎麼發現的（2026-05-14，T-073 security review）

T-073 把 SPA 的 Google 按鈕從「直連 bare source-init URL」改成「走 `cf-google-init` flow-executor URL」。security-engineer subagent review 問「現在 `next` 真的被 honor 了，是不是 open-redirect」。追 Authentik 2024.12.5 source 確認：`_flow_done` 的 `PLAN_CONTEXT_REDIRECT` path 確實不驗證。但同時確認這是 **core 既有行為、每條 flow-executor URL 都有**，T-073 沒引入也沒加劇 → defer 開本單。

### 修法傾向

Authentik expression policy 可以讀 `request` / flow context。綁在 flow 上、`evaluate_on_plan` 階段檢查 `next` 的 host 是否在 allowlist（空 host = relative = OK；同 host = OK；其他 = deny 或改寫）。實作時對照 memory `authentik_blueprint_2024_12_gotchas` 的 policybinding 陷阱。
