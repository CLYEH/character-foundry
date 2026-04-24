# T-026: E2E — Character creation smoke test (template mode)

**Status:** TODO
**Sprint:** 2
**Est:** S (1h)
**Depends on:** T-012 (Playwright 設定), T-014 (stub AI), T-020, T-021, T-022, T-025
**Related:** 之後每 sprint 收尾也加 E2E

---

## Scope

E2E 跑一遍完整 Sprint 2 的 template-mode flow：登入 → Dashboard empty state → 建 Character → session 產 checkpoint → 選為 Base → 跳到 Character Detail。

**In scope:**
- 新增 `web/tests/e2e/character-creation.spec.ts`
- 測試步驟：
  1. 用 seed user `test+alice@internal.local` 登入
  2. Dashboard 看到 empty state + CTA
  3. 點 CTA → `/characters/new`
  4. 填 name "E2E 角色"、選 Template → 送出
  5. Session 頁載入
  6. 選 menu（性別 / 髮型 / 風格）+ 填 freeform「穿旗袍」
  7. 點 `[生成]` → 等 task 完成（stub AI，~2 秒）
  8. checkpoint 卡片出現、status=completed
  9. 點 `[選作 Base]` → confirm → 確認
  10. Redirect 到 `/characters/{id}` 看到 Base 圖 + alias/motion empty state
- Seed 清理：test 結束後透過 backend 清除該 user 的 character（或用 unique name 避免衝突）
- CI 整合：在 T-012 已經設好的 e2e job 裡多跑本 spec
- `AI_STUB_MODE=true` 必須 CI env 設好（T-014 應已處理）

**Not in scope:**
- Reference mode E2E（可留到 Sprint 3 或 polish）
- Cancel flow E2E
- Advanced prompt modal E2E
- Failed task recovery E2E

---

## Planning refs

- `planning/ux/user-flows.md` §4.1 Flow A
- `planning/devops/ci-cd.md` §3 e2e job
- T-012 spec（Playwright 已設定好）

---

## Acceptance criteria

- [ ] `pnpm -C web e2e character-creation` 本機跑過
- [ ] CI e2e job 新增本 spec，總時間仍 < 5 分鐘
- [ ] Flaky rate < 2%（連跑 5 次全綠）
- [ ] 失敗時 CI artifact 含 trace + screenshot + video
- [ ] 清理：test 結束後 character 不殘留（用 cleanup fixture or API call）

---

## Files expected to touch

- `web/tests/e2e/character-creation.spec.ts` (new)
- `web/tests/e2e/fixtures/characterSeed.ts` (new) — seed + cleanup helpers
- `api/app/cli.py` (edit) — 若需要 E2E 專用 cleanup CLI command
- `.github/workflows/pr.yml` (edit 若 e2e job 需要調整 env)

---

## Notes

- Stub AI 的 sample PNG 跟 production PNG 都是透明 PNG，visual assertions 只看有無圖、不做像素比對
- Checkpoint 完成的判斷：等 `[data-testid="checkpoint-card"][data-status="completed"]` 出現，timeout 30s
- Test 用 `test+sprint2@internal.local` 比較清楚（跟 T-012 的 alice 分開）
- 不要在 E2E test 裡測超多 edge case；這是 smoke test，保證 happy path 不 regress 即可
- Retry policy：Playwright config 已設 `retries: 2 on CI`
