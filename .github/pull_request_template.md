<!--
PR title 格式：T-XXX: <一句話>  或  <type>: <描述>
範例：T-006: backend auth (JWT login/refresh/logout/me)
-->

## Ticket

Ticket: `T-XXX`

<!-- 若連動 GitHub Issues 也可以用 Closes #123 -->

---

## Scope

<!-- 一句話說清楚這個 PR 完成了什麼（對應 ticket scope）-->

---

## Changes

<!-- 條列關鍵改動，不用逐檔列（git diff 會說話），只列架構 / 行為層面的變更 -->

-
-
-

---

## Testing

<!-- 怎麼驗證這個 PR 是對的。有 unit / E2E / manual QA 分開說 -->

- [ ] Unit tests pass (`pytest api/tests/...` / `pnpm test`)
- [ ] E2E tests pass (`pnpm e2e`)
- **E2E coverage gate**（CONTRIBUTING §3.5）—— 三選一：
  - [ ] 本 PR 已新增 / 更新對應 Playwright spec（routing / 新頁面 / critical user action 改動）
  - [ ] Deferred to `T-XXX`（目標 ticket 必須已存在於 tickets/ 或 STATUS.md backlog；下方 description 同步說明）
  - [ ] N/A —— 勾選一個 N/A 理由：backend-only / docs / pure refactor / CSS-only / spike
- [ ] Manual QA 描述：
  -

---

## Screenshots / Recordings

<!-- UI 變動必附前後對比。若為 backend-only PR 可刪除此區 -->

---

## Codex Review 回應

<!--
Codex App 會在 PR 開出後自動 review 並留 comments（無需手動觸發）。
合併前所有 critical comments 必須處理：
  - 採納 → 改 code 推新 commit
  - 駁回 → 在該 comment thread 回覆理由
  - Defer → 開新 ticket，在 comment 回覆 "deferred to T-xxx"
-->

- [ ] Codex 自動 review 完成
- [ ] 所有 critical comments 已回應（採納 / 駁回 / defer）

---

## Checklist

- [ ] Ticket status 已更新（in_progress → done if 完成）
- [ ] `STATUS.md` 已同步
- [ ] 若完成 ticket：`git mv tickets/T-XXX-*.md tickets/DONE/`
- [ ] Planning docs 有需要同步更新嗎？（通常不用，若 API shape / schema 改了才需要）
- [ ] 沒有意外 commit `.env` 或其他 secret
- [ ] CI 綠
- [ ] Codex critical comments 都已處理（見上方）
- [ ] 命名遵循 `CONTRIBUTING.md` §1-2 的 branch / commit / PR title 規則

---

## Notes for reviewer

<!-- 可選。給 reviewer 的 hint：值得注意的 trade-off、需要特別看的地方、還沒做的部分 -->
