# T-049: Require e2e happy path for routing / new-page / critical-action PRs

**Status:** TODO
**Sprint:** 3 (process / governance — out of feature scope)
**Est:** XS
**Depends on:** none
**Related:** T-041（既存 backlog 的 alias/motion e2e catch-up；本單通過後它的存在意義從「補課」升級成「示範如何補課」）

---

## Scope

把目前的隱性實踐「e2e 用獨立 ticket 批次補」改成顯性 process gate：所有觸及 routing / 新頁面 / critical user action 的 PR 必須附帶對應 Playwright e2e happy path，否則必須顯式 defer 並 link 到目標 ticket。

**動機（佐證見 PR 摘要）：**
- `git log --diff-filter=A -- 'web/tests/e2e/*.spec.ts'` 顯示只有 T-012 / T-026 兩次 spec 新增；中間 9 張 frontend 單（T-021 ~ T-040）shipped 時都沒同步補
- T-041 backlog 的存在本身就是 process drift 的證據——alias/motion 已經迭代多次後才回頭補 e2e，context 重建成本 > 當下寫的成本
- 既有 CI（`.github/workflows/pr.yml:106-209`）已經把 Playwright 跑齊，infra 不缺

**In scope:**
- CONTRIBUTING.md 新增 §3.5「E2E coverage 必填條件」
- `.github/pull_request_template.md` 把 Testing 區的「E2E（若適用）」改成顯性 gate checkbox
- STATUS.md 加本單

**Not in scope（保留給其他單）：**
- 補 T-021 ~ T-040 的歷史 e2e 缺口（那是 T-041 + 後續 catch-up ticket 的事）
- CI 自動偵測 routing diff 並強制 fail（過早自動化；先靠 PR template + reviewer / Codex 把關）
- Backend route / API 的 contract test gate（不同議題，留給 swagger 規劃時談）

---

## Planning refs（開工前必讀）

- `CONTRIBUTING.md` §3.2 / §3.4 — Testing 描述、Draft PR 機制（本單會擴充 §3 的 E2E 規範）
- `CONTRIBUTING.md` §4.5 — Codex defer 機制（本單的 defer-to-ticket pattern 沿用相同精神）
- `.github/workflows/pr.yml` lines 106-209 — 既有 Playwright e2e job（證明 infra 已就緒）
- `web/playwright.config.ts` — chromium-only / workers:1 / retries:1 設定，本單不動
- `tickets/T-041-e2e-alias-motion-flow.md` — 既有 catch-up ticket，本單通過後的 defer pattern 參考

---

## Acceptance criteria

- [ ] CONTRIBUTING.md 新增 §3.5（或合理位置）寫清楚：哪些情境必填 e2e、哪些 N/A、defer 路徑（含 link 到目標 ticket + STATUS.md backlog）
- [ ] PR template 在 Testing 區把 e2e 從「optional」改成「明確三選一 gate（已加 / deferred / N/A）」
- [ ] STATUS.md 加 T-049 並標 DONE
- [ ] PR description 引用 evidence（git log / 缺口 ticket 列表）
- [ ] 本 PR 自身 N/A e2e（純文件改動）→ 順便當第一個示範案例

---

## Files expected to touch

- `tickets/T-049-require-e2e-for-routing-changes.md` (new → DONE on completion)
- `CONTRIBUTING.md` (edit — 加 §3.5)
- `.github/pull_request_template.md` (edit — Testing 區 + 主 checklist 各加一條)
- `STATUS.md` (edit — Sprint 3 表格末尾加 T-049 + Last updated 行)

---

## Notes

**為什麼不做自動 enforcement：**
- 自動偵測「PR 是否觸及 routing / critical action」需要 path-based heuristic（react-router 檔案 diff、handler/mutation 偵測），偽陽 / 偽陰率高
- Phase 1 solo 下，PR template 勾選 + Codex review 已經是雙重把關（Codex 在 review 時若看到 Testing 欄寫 N/A 但 diff 含 route 改動，會 flag）
- 之後若觀察到規則被無視可以再升級成 GitHub Actions check

**為什麼 critical user action 不窮舉清單：**
- 列舉永遠會落後（Sprint 4/5 還沒開單）
- 改成「使用者完成主流程必經的一步」原則 + 當下範例，未來自然外推

**Defer pattern 與 Codex defer 的差別：**
- Codex defer 需要 in-code anchor（§4.5.4），因為 reviewer 是 LLM、要靠 code 表面化
- E2E defer 的 reviewer 是人 + Codex，PR description + STATUS.md 兩個 anchor 已足夠；不用再強制 in-code TODO
