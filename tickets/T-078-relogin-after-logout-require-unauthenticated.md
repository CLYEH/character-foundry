# T-078: logout 後無法重新登入 — SPA logout 不結束 Authentik session，re-login 撞 `require_unauthenticated`

**Status:** TODO
**Sprint:** Backlog（post-3.5a；real login bug — T-076 後使用者實測 reveal，wall 6）
**Est:** M
**Depends on:** none
**Related:** T-073（wall 3 ticket 已預告「若 re-login 卡關，一併處理」但 punt 了 —— 本單就是那條）、T-056（SPA OAuth logout 落地）、T-076（CDP 驗證後使用者實測 reveal）

---

## Scope

修「operator 登出後無法再登入」這道牆（wall 6）：SPA 的 logout 只 revoke OAuth token + 清本地 state，**不結束 Authentik 的 `authentik_session`**。所以 re-login 時 Google callback 回到 `default-source-authentication` flow，而該 flow `authentication = require_unauthenticated` —— operator 還握著 Authentik session → flow policy 判 "Flow does not apply to current user" → Permission denied。

### 怎麼發現的（2026-05-15，T-076 之後使用者實測）

T-076 修好 CORS 後，使用者用隱私視窗實測：fresh session 第一次登入 OK；但**登出後想重新登入就不行**。也就是 T-073 ticket 早就預告的 wall 3 第二半（`require_unauthenticated` re-login 卡關），T-073 當時 punt（「確認正常 fresh-session 流程下…若 root-cause 修好後仍有 re-login 卡關，一併處理」），現在確認是真的、可重現。

### Root cause

- `web/src/lib/oauth-client.ts` `revokeOauthToken`：SPA logout 走 RFC 7009 revoke（`/application/o/revoke/`）只作廢 refresh token，**不是** Authentik 的 OIDC end-session。docstring 自己寫「explicitly out of scope for Phase 1 (single user)」。
- 所以 logout 後 `authentik_session` cookie 還在 → operator 對 Authentik 仍是 authenticated。
- re-login：`cf-google-init` → Google → callback → `SourceFlowManager` → `handle_auth` → `_prepare_flow(default-source-authentication)`。`default-source-authentication` 的 `authentication = require_unauthenticated`（Authentik out-of-box 預設）→ `FlowPlanner.plan()` 看到 `request.user.is_authenticated` → `FlowNonApplicableException` → AccessDeniedResponse。
- 「single user 不用 OIDC end-session」這個 Phase 1 scoping 假設，正好被 single user 自己「登出再登入」戳破。

**In scope:**
1. 收斂修法（plan 時定）。候選：
   - **(a) SPA logout 也結束 Authentik session** —— logout 流程帶使用者過 Authentik 的 invalidation flow / end-session，清掉 `authentik_session`。conceptually 最對（「登出就是登出」），但要重新評估 T-056/`oauth-client.ts` 把 OIDC end-session 列 out-of-scope 的決策。
   - **(b) 放寬 `default-source-authentication` 的 `authentication` policy** —— 從 `require_unauthenticated` 改成允許 re-auth（`none` 或自訂）。Authentik flow 設定改動，可能 codify 進 blueprint。要評估對其他 source-login 情境的影響。
   - **(c) source-init 時帶 `prompt=login` / 強制 re-auth** —— 看 Authentik OAuth source 支不支援。
2. 修好後 re-login（logout → 再 login）能正常走到 Dashboard。
3. 對應更新 planning doc（`oauth-integration.md` logout 段 / `authentik-stack.md` §5.2.1）。

**Not in scope:**
- 全域 single-logout / 跨裝置 session 終結（Phase 1 single-user 不需要）。
- operator-provisioning（T-071 / T-077）。

---

## Planning refs（開工前必讀）

- `web/src/lib/oauth-client.ts` `revokeOauthToken` + docstring（現況 + 「out of scope」的決策來源）
- `planning/frontend/oauth-integration.md` §2.4（authStore refresh / logout logic）
- `planning/devops/authentik-stack.md` §5.2.1（`cf-google-init` flow + `default-source-authentication` 的角色）
- `tickets/DONE/T-073-*.md`（wall 3 ticket，已預告本單）、`tickets/DONE/T-056-*.md`（SPA OAuth logout 落地）

---

## Acceptance criteria

- [ ] operator 登入 → 登出 → **再次登入能正常走到 Dashboard**（不撞 "Flow does not apply" / Permission denied）
- [ ] fresh-session 第一次登入仍正常（不回歸 T-076 驗過的路徑）
- [ ] CDP 實測 logout → re-login 一輪（memory `reference_local_chrome_cdp_connection` / `feedback_verify_oauth_flow_via_cdp_before_ship`）
- [ ] 對應 planning doc 更新
- [ ] CI 綠

---

## Files expected to touch

- `web/src/lib/oauth-client.ts` 或 `web/src/stores/authStore.ts`（若採 (a)）
- `infra/authentik/blueprints/cf-google-init.yaml` 或 Authentik flow 設定（若採 (b)）
- `planning/frontend/oauth-integration.md` / `planning/devops/authentik-stack.md` (edit)
- `STATUS.md` (edit)

> **E2E coverage gate（CONTRIBUTING §3.5）：** 視修法 —— 若改 SPA logout code path（critical user action），更新 `web/tests/e2e/oauth-login.spec.ts` 加 logout→re-login 段；若純 Authentik flow 設定則可能 N/A。實作者於 PR 說明。注意 e2e 走 nginx:80 同源，logout→re-login 這條若 CI 環境也能覆蓋就盡量覆蓋。

---

## OAuth scope required（後端 endpoint 必填；frontend / docs / infra 票寫 `n/a`）

`n/a`

---

## MCP tool delta（agent surface 影響；無影響寫 `n/a`）

`n/a`

---

## Notes

### 為什麼這是「真 bug」而非 provisioning gap

跟 T-077（wall 5，operator 沒進 group）不同 —— wall 5 對「沒被 provision 的人」拒絕，某種程度是 access control by design。wall 6 是**已 provision 好的 operator（連 `leoyeh906` 都一樣）登出後就回不來**，是純功能性 bug，影響日常使用。優先級應高於 T-077。

### 暫時 workaround

隱私視窗每次重開，或手動清 `localhost` 的 `authentik_session` cookie。
