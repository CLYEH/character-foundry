# T-071: Backend OAuth auto-provisioning — `_resolve_oauth` lazily creates the `User` row

**Status:** TODO
**Sprint:** M3.5b（`authentik-stack.md` §5.7.2 deferred item；不 block M3.5 ship）
**Est:** S
**Depends on:** none
**Related:** T-054（dual-stack auth middleware）、T-069（`provision-operator` CLI — 本單落地後 CLI 退回 break-glass-only 角色）、T-070（dev 測試 reveal 了這個缺口）

---

## Scope

讓一個經 Authentik 驗證過的 OAuth user，**第一次打 backend API 時自動建立對應的 `users` row**，而不是 401。

現況：`api/app/api/deps.py::_resolve_oauth` 拿到 Authentik token 後走 email lookup（`select(User).where(User.email == claims.email)`），`users` 表沒有對應 row 就 `auth_invalid_token()` → 所有登入入口（Google / 帳密 fallback）都 401。目前唯一補 row 的方法是手動跑 `provision-operator` CLI（T-069）。

**In scope:**

1. **`_resolve_oauth` lazy provisioning** —— Authentik token 驗證通過、但 `users` 表沒有 `claims.email` 的 row 時，自動建一個（email + name from claims、default team），而不是 401。`deps.py` 該行 comment 已暗示未來會做這件事。
2. **Guardrail（必要）** —— 不可對任意 Authentik 驗過的 email 都自動建 row。閘門擇一（plan 時定）：
   - Workspace `hd=` domain allowlist（對齊 `authentik-stack.md` §5.2 提的 `hd=` trust anchor），或
   - 明確的 email / domain allowlist env var
   - 沒過閘門 → 維持 401（fail loud）
3. **`provision-operator` CLI 角色收斂** —— 本單落地後，CLI 從「唯一補 row 方法」退回「break-glass / 預先 provision」用途。CLI 不必刪，但 `authentik-stack.md` §5.7.2 要更新說明：正常情況下 first-login 自動 provision，CLI 只在要預先建 row 或 debug 時用。

**Not in scope**（保留給其他單）：

- Authentik 端的 enrollment flow（那是 Authentik 的職責，見 T-073）
- 多 team / team 指派邏輯（Phase 1 單 team，一律 default，見 DECISIONS §6 B5）
- JWT path 的 provisioning（JWT 走 `create-user`，dual-stack 過渡期既有行為不動）

---

## Planning refs（開工前必讀）

- `planning/devops/authentik-stack.md` §5.7.2 —— 本單就是這節「Not in scope（留 M3.5b）」描述的工作；§5.2 的 `hd=` trust anchor 段是 guardrail 設計的依據
- `planning/auth/` —— dual-stack auth 決策脈絡
- `api/app/api/deps.py` —— `_resolve_oauth` 是要改的函式；現有 comment 已點名這個未來工作

---

## Acceptance criteria

- [ ] 經 Authentik 驗證、email 通過 guardrail、但 `users` 無 row 的 OAuth token → 第一次打 API 自動建 row 並放行（不是 401）
- [ ] 不通過 guardrail 的 email → 維持 401，且 fail loud（log / error code 清楚）
- [ ] 既有有 row 的 user → 行為不變（不重複建）
- [ ] 既有 JWT path 行為不變
- [ ] 測試都綠：`pytest api/tests/` 相關（新增 auto-provision happy path + guardrail reject + 既有 row 不重建三條）

---

## Files expected to touch

- `api/app/api/deps.py` (edit) —— `_resolve_oauth` lazy provisioning
- `api/app/...` (edit) —— guardrail 設定（env var / config）
- `api/tests/...` (new/edit) —— 三條測試
- `planning/devops/authentik-stack.md` §5.7.2 (edit) —— CLI 角色說明更新
- `STATUS.md` (edit)

> **E2E coverage gate（CONTRIBUTING §3.5）：** 預期 **N/A — backend-only**。auth middleware 行為改動，無 React Router route / critical action 的前端改動。

---

## OAuth scope required（後端 endpoint 必填；frontend / docs / infra 票寫 `n/a`）

`n/a`（不新增 / 改動 endpoint；改的是 auth middleware 的 user 解析行為，不是某條 endpoint）

---

## MCP tool delta（agent surface 影響；無影響寫 `n/a`）

`n/a`

---

## Notes

T-070 的 dev CDP 測試裡，walls 連環卡的第二道就是這個：Authentik 認得 user 不等於 backend 認得。T-069 已經把 `provision-operator` CLI 做出來當手動 stop-gap，但每個新 operator 都要記得手動跑一次是 operator-persona 的摩擦點。本單把它變成 first-login 自動行為。

⚠ Guardrail 不是可選的 —— 沒有它，任何 Authentik 驗過的 Google 帳號都會在 backend 自動長出 row。Authentik enrollment flow 本身可能也有 `hd=` 限制（見 T-073 / §5.2），但 backend 不應該假設上游一定鎖好；defense in depth。
