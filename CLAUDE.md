# Character Foundry

## 專案簡介
一個網頁平台，讓使用者透過文字或圖片 AI 生成虛擬角色，
角色最終用於 AI 導覽員系統的虛擬形象（2D 動畫 / 未來支援 3D）。

詳細專案背景請見 `planning/project-brief.md`。

## 現階段 scope
- 做到：image generation（含 inpaint 編輯）、i2v（動作影片生成）
- 暫緩：lip sync、image-to-3D

## 技術棧
- Frontend: React
- Backend: Python
- AI 模型（現階段）：
  - Image generation（asset 生成）：gpt-image-2（text-to-image）
  - Image editing（asset → alias 造型編輯）：gpt-image-2（image-to-image / text-to-image / inpaint 視情境選用）
  - i2v：Veo 3.1
- AI 模型（暫緩）：lip sync、image-to-3D

## 規劃資料夾
各 agent 的規劃文件存放在 `planning/` 下：

| 資料夾 | Agent 角色 | 負責範圍 |
|--------|-----------|---------|
| `planning/product/` | Product Agent | 功能範圍、使用者故事 |
| `planning/ux/` | UX Agent | 操作流程、頁面設計 |
| `planning/frontend/` | Frontend Agent | React 架構、元件設計 |
| `planning/backend/` | Backend Agent | API 設計、AI 模型串接 |
| `planning/data/` | Data Agent | 資料模型、DB schema |
| `planning/devops/` | DevOps Agent | 部署、環境、infra |
| `planning/agent-interface/` | Agent Interface Agent | MCP server、agent-native surface（M3.5）|
| `planning/auth/` | Auth Agent | OAuth 2.1、JWT migration、MCP auth integration（M3.5）|

## 如何切換 Agent 視角
開新 session 時，告訴 Claude：
「請用 [agent 名稱] 的視角」，Claude 會讀取對應資料夾的 CLAUDE.md 進入角色。

例如：
- 「請用 product agent 的視角，對我做需求訪談」
- 「請用 backend agent 的視角，規劃 API」
- 「請用 data agent 的視角，設計資料模型」

## 平台原則
- **人機雙介面**：除了讓真人透過 UI 操作，平台也必須是 **agent-friendly** 的
- **Skill 化能力**：所有核心能力（角色生成、alias 編修、i2v 等）都要能包裝成 agent 可呼叫的 skill
- 這條原則會影響 API 設計（自描述、OpenAPI、考慮 MCP）、非同步任務狀態通知、資源 ID 規則

## 施工原則
- planning/ 下的文件是規格書，施工時以它為準
- 有衝突或模糊時，先暫停問使用者
- 目標是完整產品，不是 MVP

## 新 Session 工作流程

### 做規劃 / Planning 類任務
1. 使用者會說「請用 [agent 名稱] 的視角」
2. Claude 讀對應 `planning/<agent>/CLAUDE.md` 進入角色
3. 照需求討論、更新 planning 文件

### 做實作 / Feature 類任務（常見）
1. 使用者會說「我要做 T-XXX」、「繼續做 T-XXX」或（在 worktree 內）「start T-XXX」
2. Claude **必讀順序：**
   - `CLAUDE.md`（專案定位）
   - `DECISIONS.md`（核心決策快查）
   - `CONTRIBUTING.md` §1 branch 規則（若上次 session 已讀過可略，但切 branch 前務必確認）
   - `tickets/T-XXX-*.md`（本單完整內容）
   - 單裡列的 **Planning refs**（具體規格）
   - `STATUS.md`（看前後依賴）
   - 若 working dir 看起來像 worktree（路徑含 `.wt/T-XXX`、或 `git rev-parse --show-toplevel` 不是主 repo）→ 加讀 `tickets/PARALLEL_WORKFLOW.md` §4 ~ §7（pre-flight、上游檢查、每 loop tick rebase、conflict SOP）。Worktree 情境下「start T-XXX」== 走那份 SOP 一路到 merge。
3. **切 ticket branch（必做；動手改檔之前）：**

   Branch 命名是 `<type>/T-XXX-short-desc`，`<type>` 依 CONTRIBUTING §1.1 取 `feature` / `fix` / `chore` / `refactor`——**先根據 ticket 性質決定 type**，再用下面指令（把 `<type>` 換成實際值）：

   ```bash
   git branch --show-current
   git fetch origin
   git branch --list '*/T-XXX-*'                 # 本地有沒有任何 type 的 T-XXX branch
   git ls-remote --heads origin | grep '/T-XXX-' # 遠端有沒有
   ```

   - **新 ticket**（都沒找到）→ 從最新 main 切一條：
     ```bash
     git switch main && git pull
     git switch -c <type>/T-XXX-short-desc   # 命名慣例見 CONTRIBUTING §1.1
     ```
   - **繼續做 ticket**（已存在於本地或 origin）→ 切到既有 branch，不要 `-c`：
     ```bash
     git switch <type>/T-XXX-short-desc      # 本地已有就直接切
     # 若只有 origin 有，git switch 會自動建立 tracking branch
     ```

   ⚠ 若 `Current branch: main` 出現在 session 開場的 git status，**這不是可以直接動工的狀態**，要先切 branch。Auto mode 不例外——這是 pre-flight 不是 deliberation。
4. 實作 → 測試 → commit（commit message 格式見 CONTRIBUTING §2）
5. 完成時：
   - `git mv tickets/T-XXX-*.md tickets/DONE/`
   - 更新 `STATUS.md` 該單狀態與 milestone 進度
   - 若有發現新問題不在 scope：開新單（或記到 STATUS.md backlog）
   - Push + 開 PR（模板見 `.github/pull_request_template.md`）
6. **PR 開完後自動 loop 等 Codex review（必做；不要丟給使用者）：**

   `/loop 10m <內容>`——每 10 分鐘 tick 一次，判讀**只看 PR body 上 Codex（`chatgpt-codex-connector[bot]`）的 reactions**（`/issues/N/reactions`）：

   1. **`+1` reaction** → **先檢查 CI 是否全綠**（見下方 §Merge gate），全綠才 `gh pr merge N --squash --delete-branch` 停 loop；有任何 FAILURE / SKIPPED → 不 merge，照「CI 紅」分支處理
   2. **`eyes` reaction** → 繼續 loop（Codex 還在審）
   3. **無任何 reaction 且無 Codex comment（issue-level / inline / review record 都沒有）** → `gh pr comment N --body "@codex review"`，繼續 loop

   **Merge gate（必查；不依賴 GitHub branch protection）：**

   ```bash
   gh pr view N --json statusCheckRollup \
     -q '.statusCheckRollup[] | "\(.name)\t\(.conclusion)\t\(.status)"'
   ```

   - 每個 check 的 `conclusion` 必須是 `SUCCESS`（或 `NEUTRAL`），且 `status` 是 `COMPLETED`
   - 任何 `FAILURE` / `CANCELLED` / `TIMED_OUT` / `ACTION_REQUIRED` → CI 紅，不 merge
   - `SKIPPED` 也算紅（通常是上游 check FAIL 導致 dependent job 沒跑；意味著該 check 沒給 signal）
   - 若還有 `IN_PROGRESS` / `QUEUED` / `PENDING` → 繼續 loop 等
   - 沒有任何 check 也算紅（保險：repo 應該至少要有 PR workflow 跑 lint/test）

   ⚠ **「沒有任何 check」可能是 PR merge conflict 造成的**（GitHub 對 `mergeable: CONFLICTING` 的 PR **靜默跳過 CI workflow**，不會回任何 check）。Loop tick 看到 0 check 時加查：

   ```bash
   gh pr view N --json mergeable,mergeStateStatus
   ```

   - `mergeable: CONFLICTING` / `mergeStateStatus: DIRTY` → 真正原因是 conflict，**不是 CI 壞掉、不是 billing、不是 concurrency limit**。修法是 rebase onto `origin/main` 解 conflict 後 force-push，CI 會自動重跑
   - `mergeable: MERGEABLE` 但無 check → 才是真的 CI 沒觸發，可能 workflow 條件 / billing
   - `mergeable: UNKNOWN` → GitHub 還在算，等下個 tick 再查

   來源：T-033 worktree agent 2026-04-30 踩過——CI 神祕停跑於單一 PR 時，先查 mergeable，省去追 billing / concurrency / workflow 假說的時間。

   理由：branch protection 不一定有設好 required checks，Codex `+1` 也只代表程式碼層次的 review pass，不代表 tooling/CI pass。merge red PR 會把壞 main 推給後續 ticket。Loop 必須自己把關。

   **CI 紅且 Codex `+1`（衝突情境）：** 不 merge。判讀 CI failure log（`gh run view <run-id> --log-failed`），是 flake → 重跑（`gh run rerun <run-id>`）；是 real failure → 推 fix commit，繼續 loop（CI 重跑後 Codex 也會再 react，重新走完整流程）。**不要因為已經有 `+1` 就跳過修 CI**——`+1` 對應的是當時 commit 的 review，新 commit 推上去 Codex 會重新評估。

   **Codex 留 critical comment / `-1`（但沒 `+1`）→ 不 merge**，採納 / 駁回 / defer + 推 fix commit + 回覆該 thread，繼續 loop。⚠ 採納前 cross-check：Codex 意見可能和 Codex App 文件 / CONTRIBUTING / observable 行為衝突；第二意見不自動更權威，理由站不住就駁回並在 thread 說明。

   起首 tick 不要等 cron，當前 turn 也跑一次。Reaction / API 端點細節見 `codex_reaction_semantics.md`、`reference_github_pr_comment_endpoints.md`（memory）。

### 開新 ticket
用 `tickets/_TEMPLATE.md` 複製改寫。編號接上次最大 + 1。

## 關鍵入口文件

| 檔案 | 用途 |
|---|---|
| `DECISIONS.md` | 載入 80% context 的快查 |
| `STATUS.md` | 當前進度與 backlog |
| `CONTRIBUTING.md` | Git / PR / code review 規則 |
| `tickets/README.md` | Ticket 工作流與慣例 |
| `tickets/T-XXX-*.md` | 個別單的 scope + acceptance |
| `.github/pull_request_template.md` | PR 模板（commit 前參考）|
| `planning/*/` | 各 agent 的完整規格書 |
