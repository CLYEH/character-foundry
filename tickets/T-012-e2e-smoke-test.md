# T-012: E2E Smoke Test (Playwright)

**Status:** TODO
**Sprint:** 1
**Est:** S (1h)
**Depends on:** T-008, T-010, T-011
**Related:** 每個 sprint 結束前都會加 E2E

---

## Scope

Playwright 基礎設定 + 第一個 smoke test：登入 → 看到空 Dashboard + TopNav。串進 CI。

**In scope:**
- Playwright setup（`playwright.config.ts` + install browsers）
- Docker compose 整套啟動的 test fixture
- Seed data script：CI 跑前建 1 個 default team + 2 個 test user
- 第一個 E2E spec：
  - `tests/e2e/auth.spec.ts` — login 成功 → 進 Dashboard → 看到 TopNav + 使用者名稱 → logout → redirect login
- 擴充 CI：在 PR workflow 加 `e2e` job（docker compose up → run playwright → collect report）
- Playwright HTML report artifact（失敗時上傳）

**Not in scope:**
- 其他 flow 的 E2E（Sprint 2 之後 feature 單自帶）
- Visual regression testing（Phase 2）
- Load testing（Phase 2）

---

## Planning refs

- `planning/devops/ci-cd.md` §3 e2e job YAML 參考
- `planning/frontend/architecture.md` §7.2 Playwright 策略
- `planning/ux/user-flows.md` §4.1 Flow A（本單先測 login 的部分）

---

## Acceptance criteria

- [ ] `pnpm e2e` 本機跑過
- [ ] CI PR workflow 加 `e2e` job，在 full stack 起起來後跑 Playwright
- [ ] E2E spec `auth.spec.ts` 涵蓋：login 成功 → Dashboard、login 失敗 → 顯示錯誤、logout
- [ ] 失敗時 CI artifact 可下載 HTML report + screenshot + video
- [ ] CI 跑 e2e 總時間 < 3 分鐘（stack 啟動 + test 執行）
- [ ] Seed script 本機跑也 work：`python -m api.app.cli seed-e2e`

---

## Files expected to touch

- `web/playwright.config.ts` (new)
- `web/tests/e2e/auth.spec.ts` (new)
- `web/tests/e2e/fixtures/index.ts` (new) — 共用 fixture（loggedInPage 等）
- `web/package.json` (edit) — 加 `e2e` script、`@playwright/test` devDep
- `api/app/cli.py` (edit) — 加 `seed-e2e` command（建 test team + users）
- `infra/docker-compose.ci.yml` (new) — CI 用輕量 compose（無 nginx 或有）
- `.github/workflows/pr.yml` (edit) — 加 e2e job
- `.gitignore` (edit) — 加 `web/playwright-report/`, `web/test-results/`

---

## Notes

- Playwright 用 `baseURL` 配 `PLAYWRIGHT_BASE_URL` env var
- Test user 建議 `test+alice@internal.local` / `test+bob@internal.local`，密碼 `TestPassword123!`
- Seed 產生 idempotent（重跑不會炸）
- CI 裡 `pnpm exec playwright install --with-deps` 是關鍵（裝 browser + system deps）
- `--trace on-first-retry` 讓失敗有 trace 可看
- E2E 跑前 wait `/health` 回 ok 再 start（避免 race）
- Screenshot / video / trace 做成 CI artifact，失敗才上傳節省空間
