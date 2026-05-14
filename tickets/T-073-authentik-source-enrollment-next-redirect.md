# T-073: Authentik source enrollment flow doesn't redirect back to the SPA — first-time operator login dead-ends

**Status:** TODO
**Sprint:** Backlog（post-3.5a；operator-persona pass amendment，同 T-068/T-069/T-070 family）
**Est:** S
**Depends on:** none
**Related:** T-069（OAuth Source 的 flow 設定 —— 本單是它的下一層）、T-070（dev CDP 測試 reveal 了這個 wall 3）

---

## Scope

修「**真人 operator 第一次**用 Google 登入時，Authentik source enrollment flow 完成後落在 Authentik 的 `/if/user/` 頁面，而不是 redirect 回 SPA」這個 dead-end。

這是 `planning/CLAUDE.md` §3 描述的 operator-amendment：T-069 把 OAuth Source 的 Authentication / Enrollment flow **指派** 補上了，但沒涵蓋「enrollment flow 完成後 `next` 有沒有被帶回去」這一層。

### 現象（T-070 CDP 測試 2026-05-14 實測）

設好 §5.2 的 enrollment flow 後，從 `:5173` 點「使用 Google 登入」：

1. **第一次登入**：source-init → Google → callback → 無 matching user → 走 `default-source-enrollment`（PromptStage 填 username → UserWriteStage 建 user → UserLoginStage 登入）→ **落在 `http://localhost/oauth/if/user/`**，不是 redirect 回 stashed `next`（authorize URL）→ SPA 永遠拿不到 code，operator 卡在 Authentik user 頁。
2. **operator 回 SPA 重點一次**：此時 user 已存在 → email_link 匹配 → 走 `default-source-authentication`。但該 flow 的 `authentication = require_unauthenticated`（Authentik out-of-box 預設），而第 1 步的 UserLoginStage 已經給了 operator 一個 active Authentik session → flow policy 判定 **"Flow does not apply to current user" / Permission denied**。

→ 兩步合起來：**operator 第一次登入完全走不完**。

**In scope:**

1. **Root-cause `next` 沒被 enrollment flow 帶回去** —— source-init view 把 `next` stash 進 session，authentication flow 會 honor，但 enrollment flow 這條沒有。查是 flow context 沒傳、flow 設定問題、還是 Authentik 2024.12.5 的行為。可能修法（plan 時收斂）：
   - enrollment flow 末端加 redirect 行為 / 調整 stage
   - 改用 blueprint 把 source + flow 設定 codify（對齊 memory `authentik_blueprint_2024_12_gotchas`；順便讓 wall 1/2/3 不會每次 Authentik DB reset 就重來）
2. **釐清 `require_unauthenticated` 的互動** —— 確認正常 fresh-session 流程下，第 1 步若正確 redirect 回 SPA，operator 就不需要「重點一次」，`require_unauthenticated` 也就只在真正 logged-out 的 re-login 場景生效（正常）。若 root-cause 修好後仍有 re-login 卡關，一併處理。
3. **更新 `planning/devops/authentik-stack.md` §5.2** —— 補上 enrollment flow 的 `next`-propagation 注意事項 + verification step（讓後人讀的是修好的版本）。§5.9 checklist 同步。

**Not in scope**（保留給其他單）：

- backend `User` row 的 auto-provisioning（T-071）
- vite dev-proxy（T-070 已 land）
- Google OAuth client / consumer-vs-Workspace 帳號的 `hd=` 限制（§5.2 已標為未來 hardening）

---

## Planning refs（開工前必讀）

- `planning/devops/authentik-stack.md` §5.2 / §5.7 / §5.9 —— OAuth Source flow 設定 + operator provisioning runbook；本單要補完 §5.2 的 `next`-propagation 缺口
- `planning/CLAUDE.md` §3 —— operator-amendment 的開單慣例（本單就是這個 pattern）
- `planning/frontend/oauth-integration.md` §1.2 —— SPA 端的 Google direct flow 圖（`next` 是怎麼一路傳的）
- memory `authentik_blueprint_2024_12_gotchas` —— 若採 blueprint 修法的已知陷阱

---

## Acceptance criteria

- [ ] 真人 operator（fresh browser、無 Authentik session）第一次點「使用 Google 登入」→ enrollment 完成後 **redirect 回 SPA**（拿到 code → token → Dashboard），不落在 `/if/user/`
- [ ] 既有 user 後續登入（fresh session）→ 仍正常走 `default-source-authentication` → 回 SPA
- [ ] `authentik-stack.md` §5.2 / §5.9 已更新，反映修好的設定 + verification step
- [ ] Manual：在 `:5173` 跑一次「全新 operator 從零登入」走到 Dashboard（可沿用 T-070 的 CDP harness；注意要清掉 Authentik session cookie 模擬 fresh browser）

---

## Files expected to touch

- Authentik flow / source 設定（admin UI、`ak shell`、或 blueprint 檔 —— 視 root-cause 修法）
- `planning/devops/authentik-stack.md` §5.2 / §5.9 (edit)
- `infra/authentik/blueprints/` 之類（若採 blueprint 修法 — new）
- `STATUS.md` (edit)

> **E2E coverage gate（CONTRIBUTING §3.5）：** 預期 **N/A** —— 改的是 Authentik flow 設定 / 文件，不是 React Router route / SPA code。既有 e2e（走 nginx:80 + seeded e2e user，不走 enrollment）不受影響且須維持綠。dev-only 的「真人 operator 首登」難用現有 e2e harness 覆蓋（harness 用 `seed-e2e` 種好的 user，跳過 enrollment）。實作者若不同意此判定，於 PR 說明。

---

## OAuth scope required（後端 endpoint 必填；frontend / docs / infra 票寫 `n/a`）

`n/a`（Authentik flow 設定 + 文件；不碰 backend endpoint）

---

## MCP tool delta（agent surface 影響；無影響寫 `n/a`）

`n/a`

---

## Notes

### 怎麼發現的（2026-05-14，T-070 CDP 測試）

T-070 修好 vite dev-proxy 後，用 CDP 連本機真實 Chrome 驗「`:5173` Google 登入」end-to-end。proxy hop 全通（`:5173` → nginx → Authentik → Google → callback 都正常，Google 發了真的 auth code）。但接著連環撞三道 operator-config wall：

- **wall 1** — OAuth Source 沒設 enrollment flow → T-069 已文件化、本次 dev 已 `ak shell` 補上
- **wall 2** — backend 無 `User` row → T-069 的 `provision-operator` CLI 補上 → 結構性修法見 T-071
- **wall 3（本單）** — enrollment flow 完成不 redirect 回 SPA + `require_unauthenticated` 的 re-login 卡關

wall 1/2 是 T-069 runbook 已涵蓋的（只是 dev 環境還沒做）；**wall 3 是 runbook 沒寫到的真缺口**。

### 為什麼 CI 沒抓到

e2e 用 `seed-e2e` 直接把 e2e test user 種進 Authentik + backend，**跳過 enrollment flow**。所以「真人 operator 第一次走 enrollment」這條路徑 CI 完全沒覆蓋。典型 operator-persona gap（`planning/CLAUDE.md` §1），同 T-068 / T-069 / T-070 family。

### 修法傾向

T-073 plan 時認真評估「Authentik 設定 codify 成 blueprint」這條：wall 1/2/3 都是手動 admin-UI / `ak shell` 設定，Authentik DB 一 reset（`authentik-stack.md` §5.8 說 DR = 重設）就全部要重來。blueprint 是 Authentik 的宣告式 / 版控機制，專案 memory `authentik_blueprint_2024_12_gotchas` 顯示已經在用。把 source + flow 設定 codify 一次解決「每次重設都踩同樣三道牆」。但 blueprint 有自己的 silent-failure 陷阱（見 memory），成本要 plan 時估。
