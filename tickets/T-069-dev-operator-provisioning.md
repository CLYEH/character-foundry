# T-069: Dev operator provisioning — Authentik source flows + operator user seed

**Status:** TODO
**Sprint:** Backlog（post-3.5a 設定缺口；dev 登入 reveal）
**Est:** S
**Depends on:** none（修的是 T-053 留下的設定缺口）
**Related:** T-053（建 Authentik stack + Google OAuth Source 的單）、T-068（SPA 三入口登入；本單是它 dev 測試時 reveal 的下游缺口）

---

## Scope

讓一個**真人 operator**（不是 e2e test user）能在 dev stack 從頭走完 SPA 登入。現況 dev 測試時三道牆連環卡死，全是 T-053 setup 留的洞，不是 T-068 的 code bug。

**In scope:**

1. **修 `planning/devops/authentik-stack.md` §5.2** —— OAuth Source 建立步驟漏了設 flow。補上：
   - **Authentication flow**: `default-source-authentication`（matched user 走這條；不設的話連匹配成功的 user 都登不進去）
   - **Enrollment flow**: `default-source-enrollment`（沒 matched user 時建新 user；不設就是現在這個 `Source is not configured for enrollment` 錯誤）
   - 補一句說明這兩個 flow Authentik 預設就有（`designation` 各為 `authentication` / `enrollment`），直接選即可
2. **新增「provision dev operator」設定步驟** —— 寫進 `planning/devops/authentik-stack.md`（§5 開頭 prerequisites 或新開 §5.7）。一個真人 operator 要能登入需要**兩層** user，目前都沒有文件：
   - **Authentik user**：enrollment flow 設好後，operator 首次 Google 登入會自動 enroll（`email_link` 之後就匹配得到）。文件講清楚「第一次登入會跳 enrollment 選 username」這個預期行為
   - **Backend `User` row**：`api/app/api/deps.py::_resolve_oauth` 走 email lookup（`select(User).where(User.email == claims.email)`），沒 row 就 `auth_invalid_token()` → `/api/v1/auth/me` 401。`seed-e2e` 只種 alice/bob/sprint2，沒涵蓋真人 operator。文件要列：`docker compose exec api python -m app.cli create-user --email <operator-email> --name <...> --team <...>`（`create-user` CLI 已存在，`api/app/cli.py:68`）
3. **決定要不要把 operator provisioning 做成一鍵 CLI / script** —— 現在分散在 admin UI 點 + CLI 跑。可選：加一個 `app.cli` 子指令（例 `provision-operator --email ...`）只做 backend `User` row 那層；Authentik 那層留 admin UI（一次性、且涉及 Google consent，難全自動）。由施工者判斷值不值得，不值得就純文件化。

**Not in scope**（保留給其他單）：

- **`_resolve_oauth` 自動 first-login provisioning**（Authentik 已驗證的 user 第一次打 API 時自動建 backend `User` row）—— `deps.py` 那行 comment 已經寫了「either the user hasn't completed first-login provisioning yet」暗示未來會做，但那是 dual-stack migration 的一塊，scope 比本單大，留 M3.5b
- **Workspace `hd=` domain restriction** —— §5.2 已標「dev 用 personal Gmail、no hd= restriction」是刻意 dev 取捨；prod 才需要，不在本單動
- **e2e 加 Google source** —— e2e 走 `cf-e2e-bootstrap.yaml` 的 identification+password path，刻意不接 Google（CI 無 upstream），維持現狀
- **prod 環境的 operator provisioning** —— 本單只管 dev

---

## Planning refs（開工前必讀）

- `planning/devops/authentik-stack.md` §5.2 —— **要改的對象**：OAuth Source 建立步驟，漏了 flow 設定
- `planning/devops/authentik-stack.md` §5 開頭 prerequisites —— 新的 operator-provisioning 步驟掛這附近
- `planning/CLAUDE.md` §1 operator persona pass —— 本單就是這條 pass 的又一個 retrofit 案例（T-068 是第一個，本單是它 reveal 的下游）
- `api/app/api/deps.py::_resolve_oauth` —— backend email lookup 的實況，解釋為什麼需要 backend `User` row
- `api/app/cli.py:68` `_run_create_user` —— 既有的 `create-user` CLI，文件直接引用

---

## Acceptance criteria

- [ ] `planning/devops/authentik-stack.md` §5.2 含設 Authentication flow + Enrollment flow 的步驟，並說明不設的後果（`Source is not configured for enrollment`）
- [ ] `planning/devops/authentik-stack.md` 有一節清楚說明 operator 要登入需要 Authentik user（enrollment 自動建）+ backend `User` row（`create-user` CLI），兩層都列指令
- [ ] 若決定做 `provision-operator` CLI：`docker compose exec api python -m app.cli provision-operator --email ...` 能建 backend `User` row，且 `pytest api/tests/...` 對應測試綠；若決定不做：ticket Notes 寫明理由
- [ ] 文件改動有人能照著從零把一個真人 operator 帳號跑通（reviewer cross-check：步驟無跳號、指令可複製貼上）

---

## Files expected to touch

- `planning/devops/authentik-stack.md` (edit) — §5.2 補 flow 設定 + 新增 operator-provisioning 節
- `api/app/cli.py` (edit, maybe) — 若做 `provision-operator` 子指令
- `api/tests/...` (new, maybe) — 若做 CLI 子指令的對應測試
- `STATUS.md` (edit) — 完成時更新

---

## OAuth scope required（後端 endpoint 必填；frontend / docs / infra 票寫 `n/a`）

`n/a`（不新增 endpoint；`create-user` / `provision-operator` 是 CLI，不走 HTTP）

---

## MCP tool delta（agent surface 影響；無影響寫 `n/a`）

`n/a`

---

## Notes

### 根因（2026-05-14 dev 測試 reveal）

Operator 在 dev stack 按 SPA 的「使用 Google 登入」→ `authentik Logo / Bad Request / Source is not configured for enrollment`。DB 確認 `authentik_core_source` 的 `google` row：`authentication_flow_id` + `enrollment_flow_id` 都是 NULL，`user_matching_mode = email_link`。

三道牆連環卡（全是 setup 缺口，不是 T-068 code bug —— T-068 的 source-init redirect 已 curl 驗過會正確 302 到 Google）：

| 牆 | 層 | 現象 |
|---|---|---|
| 1 | Authentik `google` source 無 flow | `Source is not configured for enrollment` |
| 2 | Authentik 無 operator user | flow 設好後 enrollment 會自動建，不必手動 |
| 3 | backend 無 operator `User` row | `_resolve_oauth` email lookup miss → `/api/v1/auth/me` 401 |

Dev Authentik 現有 user：`akadmin`（`root@example.com`）、`cf-e2e-alice`、`cf-e2e-bob` + 系統 user。Backend `User` row：只有 `test+alice@ / test+bob@ / test+sprint2@`。真人 operator 兩層都沒有 —— dev stack 從來只被 e2e test user 種過，沒有「種 operator 自己」的步驟。

### 為什麼是 planning doc 缺口而非 code bug

T-053 §5.2 把 Google OAuth Source 的 name / slug / provider / consumer key+secret / scopes / user matching mode 都列了，**就是沒列 flow 設定**。Authentik 的 OAuth Source 一定要設 authentication flow（matched user 用）才能 work，enrollment flow（建新 user 用）視策略選配。§5.2 漏這步 → 照著做出來的 source 一定壞。這是 §5.2 本身要修，不是哪個 ticket 的 regression。

### 注意：password fallback 也卡 wall 3

別誤以為「改走 T-068 的帳密 fallback 就能繞過」。帳密 path 一樣會到 `_resolve_oauth` 的 email lookup，operator 沒 backend `User` row 一樣 401。Wall 3 對所有登入 path 都成立 —— 真正缺的是 operator 的 backend user，跟走哪個入口無關。
