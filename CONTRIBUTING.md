# Contributing to Character Foundry

> 團隊 / AI 協作的 Git / PR / code review 規則。
> 實作 ticket 前請快速掃過一次。

---

## 0. 核心原則

1. **一張 ticket = 一個 branch = 一個 PR**（小步快跑）
2. **Main branch 永遠可 deploy**（壞的 code 不進 main）
3. **所有改動走 PR**（含文件；禁直接 push main）
4. **CI 綠才能 merge**（lint / typecheck / test 都要過）
5. **Commit message 要讓人看得懂意圖**，不是「fix bug」

---

## 1. Branch 規則

### 1.1 命名慣例

```
feature/T-xxx-short-desc     新功能
fix/T-xxx-short-desc         修 bug
chore/T-xxx-short-desc       基礎設施、config、dependency
docs/short-desc              純文件（planning 改動通常也走這個）
refactor/T-xxx-short-desc    不改行為的重構
```

範例：
- `feature/T-001-scaffolding`
- `feature/T-006-backend-auth`
- `fix/T-014-checkpoint-race`
- `chore/upgrade-react-19`
- `docs/update-decisions`

### 1.2 規則

- **一律從最新 `main` 切**：`git switch main && git pull && git switch -c feature/...`
- **Feature branch 可以 force push**（rebase with main 保持線性）
- **Main 禁止 force push**
- Branch 命名**用 `-` 不用 `_`**（一致性）
- Short desc 控制在 3-5 個字、kebab-case、英文

### 1.3 生命週期

```
切 branch → commit 做工 → push → 開 PR → review → CI 綠 → squash merge → 刪 branch
```

Merged branch 讓 GitHub 自動刪除（Settings 開 `Automatically delete head branches`）。

---

## 2. Commit Message 規則

### 2.1 格式（Conventional Commits 輕量版）

```
<type>(<scope>): <subject>

<optional body>

<optional footer>
```

**type**（必填，小寫）：
- `feat` — 新功能
- `fix` — bug 修正
- `chore` — 基礎設施 / 工具 / config
- `docs` — 文件
- `refactor` — 不改行為的重構
- `test` — 加 / 改測試
- `perf` — 效能改善
- `style` — 格式（幾乎不用，讓 Prettier / Ruff 處理）

**scope**（選填，小寫）：
- ticket 編號：`T-001`, `T-006`
- 或模組名：`api`, `web`, `infra`, `planning`

**subject**（必填）：
- 小寫開頭
- 不加句點
- 70 字以內
- 命令句（`add X`, `fix Y`），不是過去式

### 2.2 範例

```
feat(T-006): add JWT login + refresh endpoints

fix(T-008): handle 401 during token refresh race condition

The previous implementation could trigger multiple refresh calls
when several requests hit 401 simultaneously. Added module-level
promise lock.

chore(T-004): enable ruff + mypy in PR workflow

docs(planning): update backend api-shape with FB-1 degraded services

refactor(T-003): extract updated_at trigger into shared migration helper
```

### 2.3 多個 commit 的處理

在 feature branch 裡**不用過度整理**（coding 過程有 WIP commit 沒差）。
PR merge 時**squash** 成一個乾淨的 commit，subject 套用上述格式。

---

## 3. Pull Request 規則

### 3.1 PR title

格式：`T-XXX: <一句話描述>` 或 `<type>: <描述>`（無 ticket 的情況）

範例：
- `T-001: scaffolding with docker compose + hello API`
- `T-006: backend auth (JWT login/refresh/logout/me)`
- `chore: upgrade React to 19.1`

### 3.2 PR body

**用 `.github/pull_request_template.md`**（建 PR 時自動帶入）。填這些：

- **Ticket**：`Closes T-XXX`（GitHub 若連 Issues 才有 auto-close；若用本地 tickets 就寫 `Ticket: T-XXX`）
- **Scope**：一句話說做了什麼
- **Changes**：改了哪些關鍵點
- **Testing**：怎麼驗證（unit test / E2E / manual QA 描述）
- **Screenshots**：UI 變動必附
- **Checklist**：STATUS.md 更新、planning 文件若需同步

### 3.3 大小

- **理想 PR：** < 400 行 diff、< 10 檔
- **警戒：** > 800 行 → 考慮拆
- **太小：** < 20 行又沒測試 → 合併到其他 PR

一張 ticket 太大 → 拆成多張 ticket + 多個 PR。

### 3.4 Draft PR

有以下情況先開 **Draft PR**：
- 想讓 reviewer 早點看方向
- CI 還沒過但要同步 WIP 狀態
- 需要 reviewer 幫拉個意見但還沒完稿

---

## 4. Code Review 規則

### 4.1 Reviewer 要求

**每個 PR 會被 Codex App 自動 review**（Codex App 已連結 GitHub repo）—— PR 開出後 Codex 會**自動**在 PR 上留下 review comments，不需要作者手動觸發。Codex 的建議**不一定要全部採納**，但每條都必須**有意識地回應**（採納、駁回、defer to ticket）。

人類 reviewer 要求：

| PR 類型 | 最少**人類** reviewer | 備註 |
|---|---|---|
| `feat` / `fix` / `refactor` | **1 人** approve | Phase 1 團隊小 |
| `chore` / `docs` | **1 人** approve（或 self-review 註明原因）| |
| **Security-sensitive**（auth、JWT、權限、secrets） | **2 人** approve | T-006 類 |
| **Schema migration** | **2 人** approve（至少 1 人熟 DB）| T-002, T-003 類 |
| **Production deploy 流程改動** | **2 人** approve | |

**Codex review 獨立於人類 approve 之外自動執行。** 即使 1 人 approve 條件符合，Codex 找到 critical issue 仍會 block merge（作者要修 code 推新 commit，或在 PR 裡 justify 為什麼 defer）。

### 4.2 Reviewer 責任

檢查：
- 有對應 ticket 嗎？scope 有對齊嗎？
- Planning 有對應 spec 嗎？實作有照嗎？
- 測試合理嗎？
- 有沒有引入 over-engineering？
- Commit message / PR title 符合規則嗎？
- STATUS.md 更新了嗎？
- 有 secret 不小心 commit 嗎？

### 4.3 Codex Review 流程

每個 PR 的標準流程：

```
1. 作者開 PR（含 PR template 填完）
2. Codex App 自動觸發，在 PR 上留 review comments
   （通常 PR 開出後幾分鐘內；Codex 已跟 GitHub 連好，無需手動啟動）
3. 作者逐條回應 Codex 的每個 comment：
   - 採納 → 改 code 推新 commit（Codex 看到新 commit 可能再 review 一次）
   - 駁回 → 在該 comment thread 回覆說明理由
   - Defer → 開新 ticket，在 comment 回覆 "deferred to T-xxx"
4. 人類 reviewer 看 diff + Codex comments + 作者回應
5. 人類 approve + Codex 無 unresolved critical + 其他條件滿足 → Merge
```

**不是**作者手動跑 CLI command 再貼結果。Codex 是 GitHub-integrated 的自動 reviewer。

### 4.4 其他 AI agent 輔助（選用）

除了自動觸發的 Codex review，作者或 reviewer 可**主動**用 `agency-agents` 的專家 agent 做領域深度 review：
- `security-engineer` — auth / secrets / attack surface
- `db-optimizer` — schema / index / query 效能
- `code-reviewer` — 通用 code smell / 可讀性

這些是**選用**，通常用在 push PR 前自己先過一輪、或人類 reviewer 想要第二意見時。不取代人類 approve 也不取代 Codex 自動 review。

### 4.4 Request changes vs Comment

- **Request changes**：有 blocker（測試錯、方向錯、安全問題）
- **Comment**：建議、疑問、小錯字

不要因為 style preference 或 nitpick 就 request changes。

---

## 5. Merge 策略

### 5.1 Squash merge only

所有 PR merge 都用 **Squash and merge**：
- Main history 線性、乾淨
- 一張 ticket = main 上一個 commit
- Commit message 自動用 PR title + body
- Revert 容易（一個 commit 就是一個 feature）

**禁止：** Create a merge commit、Rebase and merge（會把 feature branch 所有小 commit 帶進 main）。

### 5.2 Merge 前必備

- ✅ CI 全綠
- ✅ 至少 1 人 approve（或 2 人，按 PR 類型）
- ✅ **Codex 已自動 review 完成**，所有 critical comments 都處理（採納 / 駁回 / defer to ticket）
- ✅ 無 conflict（有就先 rebase）
- ✅ PR 作者已更新 STATUS.md
- ✅ 若 ticket 完成 → `git mv tickets/T-xxx-*.md tickets/DONE/`（可在 PR 內做或 merge 後做）

### 5.3 誰可以 merge

- PR 作者自己（得到 approve + CI 綠後）
- Reviewer（approve 後可代為 merge）

---

## 6. 保護規則（Main Branch）

GitHub Settings → Branches → Add rule `main`：

- ✅ Require pull request before merging
- ✅ Require status checks to pass before merging（勾 `pr/backend-lint-test`, `pr/frontend-lint-test`, `pr/e2e`）
- ✅ Require branches to be up to date before merging
- ✅ Require conversation resolution before merging
- ❌ Allow force pushes（關）
- ❌ Allow deletions（關）
- ✅ Require linear history（配合 squash merge）

---

## 7. Pre-commit hooks（本機）

T-004 會裝起來。本機 commit 時會自動跑：

**Backend (`api/`)：**
- `ruff check`（lint）
- `ruff format --check`（formatting）
- `mypy --strict`（type check）

**Frontend (`web/`)：**
- `eslint`（lint）
- `prettier --check`（formatting）
- `tsc --noEmit`（type check）

Hook 擋下就先修再 commit。**不要用 `--no-verify` 繞過**（除非 hook 本身壞了）。

---

## 8. 特殊情境

### 8.1 Hotfix

Prod 出事需要緊急修：
1. 從 main 切 `fix/hotfix-<short-desc>` branch
2. 最小改動 + 加對應測試
3. 開 PR 標 `Hotfix` label，CI 綠後**只要 1 人 approve 可 merge**
4. Merge 後立刻 deploy
5. 事後補 postmortem（文件到 `ops-log/postmortems/`）

### 8.2 規則本身要改

修改本文件 / planning / DECISIONS 的 PR：
- 走正常 PR 流程
- 至少 1 人 approve
- 改動影響所有人時，PR description 要 @mention 團隊成員

### 8.3 WIP / 實驗 branch

可以自由切 `wip/...` branch 做實驗，不需要 PR、不需要合回 main。這些 branch 預期會被刪掉。

---

## 9. 常用指令 cheatsheet

```bash
# 新開 feature
git switch main && git pull
git switch -c feature/T-XXX-short-desc

# 同步 main 進 feature
git fetch origin && git rebase origin/main
# （若有衝突：解完 → git add → git rebase --continue）

# Force push after rebase
git push --force-with-lease

# 合併完後清理
git switch main && git pull
git branch -d feature/T-XXX-short-desc  # 本地刪
# Remote branch 讓 GitHub 自動刪

# 看某張 ticket 的 PR 歷史
git log --oneline --grep="T-006"
```

---

## 10. 關聯文件

- `tickets/README.md` — Ticket 工作流
- `STATUS.md` — 進度追蹤
- `DECISIONS.md` — 核心決策
- `planning/devops/ci-cd.md` — CI/CD 細節
- `.github/pull_request_template.md` — PR 模板
