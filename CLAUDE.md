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
3. **切 feature branch（必做；動手改檔之前）：**

   先判斷 branch 是否已存在：

   ```bash
   git branch --show-current
   git fetch origin
   git branch --list feature/T-XXX-short-desc
   git ls-remote --heads origin feature/T-XXX-short-desc
   ```

   - **新 ticket**（branch 不存在）→ 從最新 main 切一條：
     ```bash
     git switch main && git pull
     git switch -c feature/T-XXX-short-desc   # 命名慣例見 CONTRIBUTING §1.1
     ```
   - **繼續做 ticket**（branch 已存在於本地 或 origin）→ 切到既有 branch，不要 `-c`：
     ```bash
     git switch feature/T-XXX-short-desc      # 本地已有就直接切
     # 若只有 origin 有，git switch 會自動建立 tracking branch
     ```

   ⚠ 若 `Current branch: main` 出現在 session 開場的 git status，**這不是可以直接動工的狀態**，要先切 branch。Auto mode 不例外——這是 pre-flight 不是 deliberation。
4. 實作 → 測試 → commit（commit message 格式見 CONTRIBUTING §2）
5. 完成時：
   - `git mv tickets/T-XXX-*.md tickets/DONE/`
   - 更新 `STATUS.md` 該單狀態與 milestone 進度
   - 若有發現新問題不在 scope：開新單（或記到 STATUS.md backlog）
   - Push + 開 PR（模板見 `.github/pull_request_template.md`）

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
