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

### 3.5 E2E coverage 必填條件（T-049）

下列改動的 PR **必須**附帶對應的 Playwright e2e happy path（`web/tests/e2e/*.spec.ts`），不能只勾「manual QA」：

- **新增或改動 React Router route**（新頁面、route guard 邏輯改變、redirect 行為改變）
- **新增或改動 critical user action** —— 原則是「使用者完成主流程必經的一步」。當下範例：login、建立 Character、Select Base、Alias 編輯（含 Inpaint）、Motion 生成、刪除 Character、Download ZIP 等。Sprint 4/5 之後出現的新主流程沿此原則外推。
- **改動 happy path 流程**（例如 Creation Session 的 step transitions、Prompt preview 的 confirm/cancel 路徑）

**N/A**（不需要附 e2e）：
- backend-only PR
- 純文件 / planning / ticket
- 純 refactor（行為不變、現有 e2e 仍綠）
- CSS / 視覺微調（不改 DOM 結構或互動）
- 試驗性 spike / WIP draft

#### Defer 路徑

若有合理理由不在本 PR 寫（時間壓力、需要新 fixture、e2e infra 暫時壞），三個 anchor 缺一不可：

1. **PR description** 寫一行 `E2E deferred to T-XXX`
2. **目標 ticket 已存在**（不是「之後再開」），且在 STATUS.md backlog 或當前 sprint 表中
3. **Reviewer / Codex** 看到 defer 時 cross-check 目標 ticket 真的存在且未被無限期延後

T-041（alias/motion e2e catch-up）是現存示範案例。

#### Anti-patterns

- 連續兩張 PR 都對同一塊功能 defer e2e —— process smell，需要在 STATUS.md 留 note 說明為什麼還沒做
- 把 e2e ticket 開了但永遠停在 backlog 末端 —— 等於沒 defer
- N/A 自我認證但 diff 明顯動了 routing —— Codex review 會 flag，作者要嘛補 spec 要嘛改成 defer

#### 為什麼不靠自動 enforcement

Path-based 自動偵測（diff 觸 routing 檔 → 強制要求 spec 改動）偽陽 / 偽陰率高。現階段靠 PR template checkbox + Codex review 雙重把關已足夠；觀察到被無視再升級成 GitHub Actions check。

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

#### Phase 1 solo exception

上表是**團隊 ≥ 2 人**的基準規則。現階段（Phase 1）實際只有一位活躍貢獻者，不可能湊到 2 人 approve，因此套用以下覆寫：

| 原規則 | Phase 1 solo 覆寫 |
|---|---|
| **2 人** approve（Security-sensitive / Schema migration / Production deploy） | **Codex 已完成 review 且無 unresolved critical comment** + **1 人** approve（self OK）|
| **1 人** approve（其餘類型） | 不變 |

精神：2-人 rule 存在是為了「人 × 人」cross-check。Solo 時湊不到第二個人，改由 **Codex 自動 review** 當結構性把關 —— 以 §4.3 描述的 review 完成 + critical comment 處理完成為判準（與此文件其他地方用的是同一個可驗證訊號），不綁定任何特定 reaction / bot API。

**套用條件：** 以下方 **Active maintainers** 清單為準 —— 清單只有 1 個人類 handle 時本條款生效。

**不用 git commit author 統計當判準**。Co-Authored-By trailer、GitHub `noreply` 隱私 email、同一個人的多組 git identity、bot / CI service account 都會讓 `git log --format=%ae` 的 distinct 數失真，進而在「實際上仍是 solo」的場景把這條條款誤判成失效。採用明確維護者清單可避免這類偽信號。

**Active maintainers:**
- @CLYEH — sole maintainer as of 2026-04-24

**第二位人類維護者加入時的動作（請嚴格依序）：**
1. 在上方 **Active maintainers** 清單新增其 GitHub handle
2. **刪除整個「Phase 1 solo exception」子節**
3. 同步更新 auto-merge loop 的對應 memory / 設定
4. 走正常 PR 流程取得 approve 後 merge

**不變的守門線：**
- Codex 自動 review 仍然要跑完
- Codex 若留 critical comment 必須處理（採納 / 駁回 / defer）才能 merge
- Self-approve 的 PR 作者仍要勾完 `.github/pull_request_template.md` 的 checklist
- `main` branch protection rule 不得為了這條款而放寬

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

### 4.5 Codex 溝通：什麼時候可以 push back 而不是照 review 改

Codex 是有用的第二意見，但**不是專案規則的最終裁判**。它讀靜態 diff + 通用工程慣例，沒讀過 `tickets/`、`STATUS.md`、`planning/`、不知道 sprint 怎麼切。100% 採納率是 red flag —— 代表你已經在用 Codex 蓋過自己的判斷。

下面是判斷「採納 vs 駁回 vs defer」的具體準則。三種都是 §4.3 列出的合法處理；差別在判斷 Codex 的具體意見**對不對**、**有沒有跟專案結構衝突**。

#### 4.5.1 什麼時候**直接採納**（不要 push back）

- **真實 correctness / 安全問題**：race condition、PII / 跨 session cache leak、資料完整性、SQL 注入、auth bypass、明確的邏輯 bug。
- **Codex 的建議與 repo 既有 convention 對齊**：例如 reviewer 說「應該用 `useMe` 那種 user-id-scoped queryKey」，而 `useMe.ts` 真的就是這樣寫的 → 直接採。
- **改動小、明顯改善 quality**：DRY、可讀性、命名、補測試覆蓋一個真實 edge case。沒有 scope creep。
- **Codex 點到測試是錯的或缺的**：例如 T-020 第一輪，reviewer 指出 empty-state CTA 測試其實在斷言 page-header CTA（兩個 link 同名 + findByRole exactly-one match + async render 的 footgun）—— 這是測試寫錯，馬上修。

#### 4.5.2 什麼時候**可以 push back（駁回 / defer）**

下面任何一條成立就值得寫 thread reply 而不是直接改 code：

- **Codex 技術上沒錯，但跟 ticket scope 衝突**：ticket 在 Related / Depends-on / Not-in-scope 已經把這個 concern 交給另一張單。例：T-020 dashboard PR 被 Codex flag「`/characters/new` 路由不存在會 404」，但 T-020 ticket Related 段就明寫「T-021（Dashboard 的「建立 Character」CTA 跳過去）」，T-021 / T-025 已經在 STATUS.md 排好下一兩張單做 —— 那是 incremental 設計，不是 regression。
- **採納會違反 repo 既有 convention**：例如前端慣例「每張 ticket owns 自己的 route」（T-007 → `/`、T-008 → `/login`、T-020 → `/`）；採納等於倒退這條 convention。
- **採納會製造 throwaway code**：stub route / placeholder page / temporary type，下一張 ticket 上來就要刪掉。除非那段 stub 本身有獨立價值，否則別塞。
- **採納等於 scope creep**：碰到「一張 ticket = 一個 branch = 一個 PR」這條核心原則（§0）。把下一張 ticket 的工作拉進這張 PR 不是省事，是模糊 scope。
- **Codex 的意見跟 planning / CONTRIBUTING / observable behavior 衝突**：repo 自家文件 / 自家 spec / 自家測試是權威，generic reviewer 不是。

⚠ **每次 push back 都要 cross-check**。要去讀 ticket、STATUS.md、planning 對應段落、相關檔案，**確定**你比 Codex 更了解這個情境。理由站不住就採納；只是「我覺得我對」不夠。

#### 4.5.3 怎麼 push back

Thread reply 三段式：

1. **Acknowledge Codex 對的部分**——技術觀察通常真的成立（例：「你沒錯，這個 commit 上 `/characters/new` 確實會 404」）。先把 reviewer 的論點覆述一遍，避免誤解。
2. **解釋為什麼採納會跟專案結構衝突**——具體 reference，**不是**「我們不想做」。常見可引：
   - Ticket Related / Depends-on 段（誰擁有這個 concern）
   - STATUS.md 上的下一張 ticket（這個 concern 何時會處理）
   - Repo convention（其他 ticket 怎麼做）
   - planning/ 裡的 spec
   - CONTRIBUTING 的 §0 / §1 原則
3. **證明 defer 不是丟掉**——指向已存在的 ticket、STATUS.md backlog 列、in-code TODO comment。三個 anchor 越完整，「deferred」越站得住。

#### 4.5.4 機制細節（避坑）

- **Codex 看靜態 state，不讀你的 thread reply**。下一次 push 它會基於新 commit 重 review，可能再次 flag 同樣的 concern。
- **Defer 要有 in-code anchor**。光靠 thread 留言不夠 sticky —— 在相關檔案放一條 TODO comment 點名目標 ticket（如 T-020 的 `App.tsx:23` 註解指 T-021 / T-025）。這樣下次 reviewer / Codex 看到的時候，code 本身就會 surface 這是已知的 deferred state。
- **Substantive thread reply 真的能改變 Codex 的立場**。T-020 PR #27 經驗：第一輪 defer 加 in-code 註解後，Codex 在下一個 commit 再次 flag 同樣的 P1；補一篇詳細 thread reply（acknowledge + 三條 reference + 直接問它「你的 P1 是針對 sprint scoping 本身還是只是針對當下 snapshot 的 dead links？」）後，Codex 改成 `+1`。所以面對 sticky 的 P1，**substantive 不等於冗長 —— 是「直接點出立場分歧的本質並要求 Codex 回應」**。
- **Phase 1 solo 下，Codex `+1` 是 merge gate**（§4.1 + auto-loop spec）。push back 是合法路徑但**沒拿到 `+1` 之前 auto-merge 不會 fire** —— 確定要 push back 後就要承擔這條 trade-off：要嘛說服 Codex 改 reaction、要嘛人類維護者手動 merge 並在 thread 留下「override 理由」（後者是 last resort，不要當常規）。

#### 4.5.5 Anti-patterns

- 反射性 100% 採納（接受率 100% 是 red flag）。
- Push back 但沒 cross-check（理由只是 vibe）。
- Defer 沒 anchor（只有 thread reply、沒 in-code 註解、沒指向具體 ticket）。
- 拿 admin override 跳過 Codex 卻不留書面理由（未來看 git history 的人會搞不懂）。

### 4.6 Request changes vs Comment

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
- ✅ 至少 1 人 approve（或 2 人，按 PR 類型；Phase 1 solo 覆寫見 §4.1）
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

### 7.1 Pre-push review gate（agent code review）

每次 push 前會跑 `engineering-code-reviewer` subagent review，由兩個對稱的 hook 把關：

| 觸發來源 | 機制 | 檔案 |
|---|---|---|
| Claude Code 內 `Bash` tool 跑 `git push` | Claude Code `PreToolUse` hook | `.claude/hooks/pre-push-review.sh` |
| Terminal 直接 `git push` | git `pre-push` hook | `.githooks/pre-push` |

兩個 hook **都預設 reject + 印 directive**，由 `CF_SKIP_REVIEW=1` 顯式 bypass。

**新 clone 一次性設定**（terminal 那條 hook 才會生效）：

```bash
git config --local core.hooksPath .githooks
```

不設的話 git 會跑 `.git/hooks/pre-push`（空的），等於 terminal 推送沒攔。`.githooks/` 是版控的，這條 config 是 per-clone。

**正常 review 流程：**
1. 在 Claude Code 裡呼叫 `engineering-code-reviewer` subagent，餵 `git diff origin/<base>...HEAD`
2. Subagent 回 🔴 blocker / 🟡 suggestion / 💭 nit
3. 採納 / 駁回逐條處理
4. Review 過了 → `CF_SKIP_REVIEW=1 git push ...`

**bypass 合理時機：** hotfix、純 docs、純 ticket 文件、review 已完成的二次 push。**禁止：** 用 bypass 取代 review 跳過正常工作流程。

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
