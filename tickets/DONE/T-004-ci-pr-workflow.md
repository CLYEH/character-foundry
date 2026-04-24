# T-004: CI — PR Workflow

**Status:** TODO
**Sprint:** 0
**Est:** S (1h)
**Depends on:** T-001（需要有 api/ web/ 結構才能跑 lint）
**Related:** T-012（E2E 會加進 workflow）

---

## Scope

建 GitHub Actions PR workflow，每個 PR 跑 lint + typecheck + unit test。讓 scaffolding 被 CI 保護。

**In scope:**
- `.github/workflows/pr.yml`
- 三個 job：backend-lint-test / frontend-lint-test / typecheck
- Postgres + Redis services 供 backend test
- Pre-commit hooks 設定（`.pre-commit-config.yaml` 給 backend，husky + lint-staged 給 frontend）

**Not in scope:**
- E2E workflow（T-012）
- Deploy workflow（Sprint 5 之後）
- Nightly workflow（Sprint 5 之後）

---

## Planning refs

- `planning/devops/ci-cd.md` §3 PR workflow YAML 參考
- `planning/devops/ci-cd.md` §8 Dev environment

---

## Acceptance criteria

- [ ] 跑一個故意壞掉的 PR（例：syntax error）→ CI 擋下
- [ ] 跑一個乾淨的 PR → CI 全綠
- [ ] Backend job 能成功跑 `ruff check`、`mypy`、`pytest`
- [ ] Frontend job 能成功跑 `pnpm lint`、`pnpm tsc --noEmit`、`pnpm test --run`
- [ ] Pre-commit hooks 本機能擋住 lint 錯誤（`git commit` 會被 block）
- [ ] CI 總時間 < 5 分鐘（scaffolding 階段沒什麼 test，之後自然會變長）

---

## Files expected to touch

- `.github/workflows/pr.yml` (new)
- `.pre-commit-config.yaml` (new) — backend 用
- `api/pyproject.toml` (edit) — 加 `ruff`、`mypy`、`pytest` 設定
- `web/package.json` (edit) — 加 `lint`、`test` scripts；加 `husky`、`lint-staged` devDeps
- `web/.husky/pre-commit` (new)
- `web/.prettierrc` (new)
- `web/eslint.config.js` (new) — flat config
- `api/.ruff.toml`（or in pyproject）(new)

---

## Notes

- 使用者若不是在 GitHub 上，把 `pr.yml` 轉成對應的 GitLab CI / Gitea Actions（邏輯一樣）
- Postgres 在 CI 用 `pgvector/pgvector:pg15` 確保有 extension
- Python 版本統一 3.12；Node 20 LTS
- 先用 `paths-ignore: ['planning/**', '**.md']` 避免純文件 PR 也跑 CI
- Frontend ESLint 設定跟 shadcn 相容（T-007 會裝 shadcn，現在先有 base eslint config 即可）
