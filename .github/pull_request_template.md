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
- [ ] E2E tests pass（若適用，`pnpm e2e`）
- [ ] Manual QA 描述：
  -

---

## Screenshots / Recordings

<!-- UI 變動必附前後對比。若為 backend-only PR 可刪除此區 -->

---

## Codex Review

<!--
合併前必跑。在 PR 或 local 用 /codex review 跑一次，把結果摘要貼這裡，
並逐條說明採納 / 駁回 / defer。
-->

- [ ] Codex review 跑過
- [ ] 所有 critical 發現已處理或明確 defer（defer 要附 ticket 編號）

摘要：
<!-- 例：Codex 提 3 個發現（2 採納、1 defer to T-XXX）。細節見 comment thread。 -->

---

## Checklist

- [ ] Ticket status 已更新（in_progress → done if 完成）
- [ ] `STATUS.md` 已同步
- [ ] 若完成 ticket：`git mv tickets/T-XXX-*.md tickets/DONE/`
- [ ] Planning docs 有需要同步更新嗎？（通常不用，若 API shape / schema 改了才需要）
- [ ] 沒有意外 commit `.env` 或其他 secret
- [ ] CI 綠
- [ ] Codex review 完成（見上方）
- [ ] 命名遵循 `CONTRIBUTING.md` §1-2 的 branch / commit / PR title 規則

---

## Notes for reviewer

<!-- 可選。給 reviewer 的 hint：值得注意的 trade-off、需要特別看的地方、還沒做的部分 -->
