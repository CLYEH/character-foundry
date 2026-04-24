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
1. 使用者會說「我要做 T-XXX」或「繼續做 T-XXX」
2. Claude **必讀順序：**
   - `CLAUDE.md`（專案定位）
   - `DECISIONS.md`（核心決策快查）
   - `CONTRIBUTING.md` §1 branch 規則（若上次 session 已讀過可略，但切 branch 前務必確認）
   - `tickets/T-XXX-*.md`（本單完整內容）
   - 單裡列的 **Planning refs**（具體規格）
   - `STATUS.md`（看前後依賴）
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

   Codex App 的行為（每次新 commit）：
   - **對每個 review 過的 commit 都留 review record**（`/pulls/N/reviews` 有一筆 `commit_id == 該 commit`, `state=COMMENTED`）——這是 commit-scoped 的 pass/fail 證據
   - 有意見時再留 inline review comment（`/pulls/N/comments` 的 `commit_id == 該 commit`）
   - 可能在 PR body 留 `+1` / `eyes` reaction，但 reaction 是 **PR-scoped 不是 commit-scoped**，stale 的 reaction 會跨 commit 殘留，**不可當 mandatory gate**

   `/loop 10m <內容>`——每 10 分鐘 tick 一次，每 tick 要做的事：
     1. 抓當前最新 commit SHA：`gh pr view N --json commits --jq '.commits[-1].oid'`
     2. 查 4 個端點：
        - `/pulls/N/reviews` — review records（含 `commit_id`）← **主要 gate 依據**
        - `/pulls/N/comments` — inline review comments（含 `commit_id`）
        - `/issues/N/comments` — issue-level comments
        - `/issues/N/reactions` — PR body reactions（輔助訊號用，不是 gate）
     3. **Merge gate**（以下全部滿足 → `gh pr merge N --squash --delete-branch` 並停 loop）：
        - **Codex 對 latest commit 送過 review record**：`/pulls/N/reviews` 有 entry 的 `commit_id == latest_sha`（commit-scoped 證據，Codex 實際看過這個 commit）
        - **最新 commit 上無 unresolved inline review comment**：`/pulls/N/comments` 裡 `commit_id == latest_sha` 的 top-level thread（`in_reply_to_id == null`）都已有我本人的後續 reply（採納 / 駁回 / defer 都算已處理）
        - `mergeable=MERGEABLE && mergeStateStatus=CLEAN`
        - CI `statusCheckRollup` 全 SUCCESS
        - 符合 CONTRIBUTING §4.1（含 Phase 1 solo exception）+ §5.2 的 approve 要求
     4. **Codex 有新 inline comment on latest commit**（或 `-1`）→ 採納 / 駁回 / defer，推 fix commit + 回覆該 thread，繼續 loop。⚠ **採納前先 cross-check**：Codex 的意見可能和 Codex App 自己的文件、CONTRIBUTING、或 observable 行為衝突。第二意見不是自動更權威的意見；理由站不住就拒絕並在 thread 說明。
     5. **Codex 對 latest commit 尚無 review record**：
        - 距離最新 commit push 時間 **< 15 分鐘** → Codex 還在審，繼續 loop
        - **≥ 15 分鐘仍無 review record**（也無 `eyes` reaction 等訊號）→ Codex 卡住了，主動戳一下：`gh pr comment N --body "@codex review"`，繼續 loop。Codex App 文件說「Mention @codex in your pull request to start a task or manually request a review」——這是 documented 觸發方式，不是 hack
   - 起首 tick 不要等 cron，當前 turn 也跑一次
   - Reaction semantics 細節見 `codex_reaction_semantics.md`（memory）；API 端點見 `reference_github_pr_comment_endpoints.md`（memory）

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
