# T-057: E2E OAuth login smoke + dual-stack 並存測試

**Status:** TODO
**Sprint:** 3.5a
**Est:** S
**Depends on:** T-056（前端 OAuth flow 必須跑得起來）、T-054（後端 dual-stack 必須接兩種 token）
**Related:** 是 Sprint 3.5a 的 ship gate

---

## Scope

兩條 Playwright E2E 確保 3.5a 整段 OAuth migration 真的活著：

1. **OAuth login smoke**：使用者按「Sign in with Google」→ Authentik consent（mock Google upstream）→ callback → Dashboard 渲染 character list
2. **JWT/OAuth 並存**：dual-stack 期間舊 JWT session 仍能呼 API；新 OAuth session 也能；兩個 session 不會互相 invalidate

**In scope:**

### 測試環境
- E2E 用 `docker-compose.test.yml`（或 override file）拉起完整 stack 含 Authentik
- Authentik upstream IdP 用 mock 取代真 Google（避免依賴外部服務）：可用 Authentik 內建 dummy auth source，或開一條 test-only path「skip Google，假設你是 user@example.com」
- Test fixture：在 Authentik 預埋一個 test user + group + scope 綁定

### Test 1：OAuth login smoke
- 開瀏覽器到 `/login`
- 按「Sign in with Google」
- 在 Authentik consent 頁按「Allow Access」
- 等 redirect 回 `/dashboard`
- 斷言：authStore `tokenSource === 'oauth'`、character list 渲染（call `/v1/characters` 200）

### Test 2：dual-stack 並存
- Tab A：用 既有 JWT login API 直接造一個 JWT session（API call 寫 storage），訪問 `/dashboard` 看 character list
- Tab B：跑 OAuth login flow（同 Test 1）
- 斷言：兩個 tab 都能正常用 API；強制 logout Tab A 不影響 Tab B；反之亦然

### 不寫的 negative case
- `code_verifier` 被竄改 → 已有 unit test 覆蓋
- state mismatch → 已有 unit test
- Authentik 掛掉 → infra alert 範圍，不在 e2e

**Not in scope:**
- MCP server flow（3.5b）
- Logout 後 token revoke 在 Authentik 側生效（觀察性測試，留給 3.5c）

---

## Planning refs

- `planning/frontend/oauth-integration.md` §1.2（流程圖）
- `planning/auth/open-questions.md` Q7（軟切換 — 並存的核心場景）
- T-049（process gate：critical-action PR 要 e2e；OAuth login 100% 是 critical-action）

---

## Acceptance criteria

- [ ] `npm run e2e -- oauth-login` 綠
- [ ] `npm run e2e -- jwt-coexistence` 綠
- [ ] CI workflow 把上面兩條納入既有 e2e job
- [ ] 失敗時截圖 + Authentik / backend log artifact 保留（既有 e2e 慣例）
- [ ] Test 跑時長 < 60s 每條（避免 CI 累積太久）

---

## Files expected to touch

- `web/tests/e2e/oauth-login.spec.ts` (new)
- `web/tests/e2e/jwt-coexistence.spec.ts` (new)
- `web/tests/e2e/fixtures/authentik.ts` (new) — Authentik test helper（pre-seed user / scope）
- `docker-compose.test.yml` (edit, 若有) — 跑 e2e 時 Authentik upstream mock 設定
- `.github/workflows/ci.yml` (edit, 視情況) — 把 Authentik service 加進 e2e job
- `tickets/T-057-e2e-oauth-login-smoke.md` (new — 本單)
- `STATUS.md` (edit) — 標記 3.5a milestone 達成

---

## OAuth scope required

`n/a`

---

## MCP tool delta

`n/a`

---

## Notes

- **為什麼不真打 Google**：CI 沒有 Google Workspace 帳號跑 OAuth；用 Authentik 內建 dummy source 或 Authentik LDAP source 接 in-memory user store。Production code path 完全不變（Authentik 與 backend 之間的合約一樣）
- **dual-stack 測試的 token 來源**：Test 2 的 JWT 直接呼既有 `/v1/auth/login` API 拿（dual-stack 期間還活著）。等 JWT path 刪除（M3.5 ship 後）時本 test 也要清掉，或改成「拿一張被 Authentik 簽發的舊 token，驗證仍能用」
- **Timing 注意**：Authentik 第一次啟動會跑 migration + 預設 group/scope/policy 建好；CI 啟動時要等 health check（per T-052），不要 race
- **Ship gate**：本單綠 = Sprint 3.5a 達成。STATUS.md 在本單 merge 後標 Sprint 3.5a DONE，可開 3.5b ticket
