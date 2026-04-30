# Character Foundry — 功能範圍（Functional Scope）

> **Status:** Draft v0.1 · 2026-04-23
> **Owner:** Product Agent
> **Scope type:** 完整產品 Phase 1（非 MVP）

---

## 1. 產品定位

Character Foundry 是**內部作業用系統**，讓團隊成員與內部 agent 透過 AI 生成可重複使用的角色素材（影像 + 動作影片）。

平台刻意保持 horizontal 設計，為後續不同的內部使用情境（AI 導覽員是其中之一）保留彈性。

**不是：** 對外商品、通用創作工具、捏臉即時體驗。

---

## 2. 核心使用者

| 類型 | 角色 | 使用方式 | 優先度 |
|---|---|---|---|
| 內部操作者 | 團隊成員（人類） | Web UI | **Phase 1 優先** |
| 下游 agent | 內部 AI 系統（如 AI 導覽員）| Export / 未來 API | **Phase 1 優先（同等）** |
| 最終觀看者 | 下游產品的終端使用者 | 不直接使用 | 體驗標準的參考 |

---

## 3. 核心實體（Entities）

```
Character（角色，最上層容器）
├─ Creation Session（建立過程，臨時）
│  └─ Checkpoints[]（迭代歷史，可分支為新 Character 的起點）
│
├─ Base（最素的模樣，一旦確立不可變）
│  └─ Motions[]（on-click 生成的動作影片）
│
└─ Aliases[]（Base 的變體，平行清單）
   └─ 每個 Alias
       └─ Motions[]（該 alias 的動作）
```

詳見 `data-model.md`。

---

## 4. Phase 1 功能清單

### 4.1 Character 建立（Creation Flow）

**F-01. 建立新 Character**
- 使用者進入建立頁面
- 選擇輸入模式：**模式 A（選單式）** 或 **模式 B（參考圖式）**

**F-02. 模式 A — 選單輸入**
- 提供下列選單（Phase 1 最少應包含）：
  - 性別
  - 眼型
  - 鼻型
  - 髮型 / 髮色
  - 膚色
  - 身材體型
  - 風格（寫實 / 動畫 / 水墨 / 日系...）
- 每個選單背後對應一段 prompt fragment
- 提供「自由補述」文字欄位
- 背後平台固定 constraints 永遠注入（transparent background, centered, facing camera directly）
- 最終 prompt = 固定 constraints + 選單 fragments + 補述

**F-03. 模式 B — 參考圖輸入**
- 使用者上傳參考圖（1 張或多張，Phase 1 決定數量）
- 提供「自由補述」文字欄位
- 背後平台固定 constraints 永遠注入
- 最終 prompt = 固定 constraints + 補述 + 參考圖（image conditioning）

**F-04a. Prompt 組合與衝突處理（LLM Reconciliation Layer）**
- 使用者的選單選項 + 自由補述 + 平台固定 constraints 之間可能衝突
  （例：使用者寫「雜亂市場背景」vs 平台固定「transparent background」）
- 透過一層 LLM（prompt reconciler）**重寫使用者補述**，移除或調整與平台 constraints 衝突的部分
- 同一層 LLM **順便把中文補述翻成英文**（見 §8 語言策略）
- LLM 輸出的結果作為最終 prompt fragment，接續與其他部分組合
- Prompt reconciler 本身是 Backend 的一個可測試獨立模組

**F-04b. Prompt 透明度**
- 最終組合出的 prompt **預設不顯示**給使用者
- UI 提供「進階檢視」按鈕 / 展開區塊，使用者可查看：
  - 平台固定 constraints
  - LLM reconciler 處理後的補述（英文版）
  - 完整最終 prompt
- 使用者無法直接編輯最終 prompt（只能改輸入：選單 / 補述 / 參考圖）

**F-04. 迭代與 Checkpoints**
- 每次點「生成」產生一張候選圖，存為 **Checkpoint**
- 使用者可：
  - **重試同 prompt**（seed 變化）
  - **用這張再改**（選某 checkpoint 當基底，改 prompt/補述/參考圖）
  - **從頭**（清空當前 creation session，重開）
- 所有 checkpoints 保留在該 creation session 內

**F-05. 確立 Base**
- 使用者從 checkpoints 中選一張「確認作為 Base」
- Base 確立後**不可修改**（只能刪除整個 Character 重建）
- 該 creation session 的所有 checkpoints 仍保留
- Checkpoints 可作為**另一個新 Character** 的建立起點（產生新 Character，不影響原 Character）

**F-06. 命名與元資料**
- 使用者為 Character 取名（必填）
- 系統自動記錄：建立時間、owner、使用的 prompt、模型版本

### 4.2 Alias 管理

**F-10. 新增 Alias**
- **一律從 Character 的 Base 當底圖**（不從其他 alias 衍生，避免錯誤累積與 identity drift）
- 支援三種輸入方式（可單獨或混合使用）：
  - **文字補述**（例：「換成紅色旗袍」）
  - **參考圖上傳**（提供外觀／配件／場景的參考）
  - **Inpaint 區域標記**（在 Base 圖上圈選要修改的區域）
- 背後依輸入組合走 gpt-image-2 的 image2image / inpaint / text2image
- 平台固定 constraints 一樣經 LLM reconciler 處理後注入
- 產出圖片，確認後加入該 Character 的 Aliases 清單

**F-11. Alias 平行組織**
- 同一 Character 底下所有 Aliases 平行並列（不階層、不繼承）
- 每個 Alias 可獨立命名（例：「紅旗袍版」「古裝版」）
- 沒有預設上限數量

**F-12. Alias 刪除**
- 可單獨刪除某個 Alias
- 刪除 Alias 會連帶刪除該 Alias 底下的所有 Motions

### 4.3 Motion 管理

**F-20. 預設動作清單（平台內建）**
- Phase 1 固定 5 種：招手歡迎、點頭說明、手勢指引、開心回應、靜置待機
- 每個 Base 和 Alias 都有這 5 個「可生成」按鈕
- 按鈕被點擊時才呼叫 Veo 3.1 生成
- 未點擊則不存在

**F-21. 自訂動作**
- 使用者可在任何時候對任何 Base 或 Alias 新增自訂 motion
- 輸入欄位（Phase 1）：
  - **動作名稱**（必填，例：「轉身揮手」、「鞠躬」）
  - **動作描述**（必填，文字 prompt，例：「慢慢轉身 180 度，然後揮手打招呼」）
- 描述會同樣經 LLM reconciler 翻成英文並注入必要 constraints 後送 Veo 3.1
- 每個自訂 motion 也會消耗 Veo 3.1 額度

**F-22. Motion 隸屬**
- 每個 Motion 綁定在一個 Base 或一個 Alias 底下
- 跨 Base/Alias 不共享（穿古裝版的招手 ≠ 穿現代裝版的招手）

**F-23. Motion 管理操作**
- 生成、預覽、重新生成（會產生新 Motion，舊 motion 保留或由使用者決定覆蓋）
- 刪除單一 Motion

### 4.4 匯出

**F-30. ZIP 下載**
- 使用者從 Character 詳細頁點「下載」
- 系統打包該 Character 的完整素材為 ZIP：
  ```
  character-{character_id}.zip
  ├── manifest.json          # Character 元資料 + 所有子項目 ID 與路徑
  ├── base/
  │   └── base.png
  ├── aliases/
  │   ├── {alias_id}.png
  │   └── {alias_id}.png
  └── motions/
      ├── {motion_id}.mp4    # Base 的 motions
      └── alias-{alias_id}/
          └── {motion_id}.mp4 # Alias 的 motions
  ```
- `manifest.json` 結構詳見 `data-model.md`

**F-31. Share link（Phase 2，暫緩）**

### 4.5 團隊與權限

**F-40. 團隊可見性**
- 團隊內所有 Characters 對所有成員**可見**
- 僅 **owner** 可編輯（新增/刪除 aliases、新增/刪除 motions）
- 其他人**不可直接修改**別人的 Character

**F-41. Copy to own workspace**
- 任何團隊成員可對他人的 Character 點「Copy」
- 產生一個**新 Character**，owner 為複製者
- **複製範圍（Phase 1 決定）**：Base + 所有 Aliases（**不含 Motions**）
- 原因：Motion 檔案量大且每個使用者的動作需求不同，複製者自己按需生成即可
- `Character.copied_from_character_id` 指向原 Character，可追溯來源

**F-42. 使用者 / 團隊模型**
- Phase 1：**單一 team / 單一 workspace**（所有人在同一個大空間）
- Schema 上**保留 `team_id` 欄位**（所有人指向同一個 team），之後加 multi-team 不用動 schema
- UI 上 Phase 1 不顯示 team 切換概念

### 4.6 Agent 介面（agent-friendly 原則）

**F-50. API 設計原則（Phase 1）**
- 所有功能（建立 Character、新增 Alias、生成 Motion、下載 ZIP）都能透過 API 呼叫
- 提供 OpenAPI spec
- 結構化錯誤（包含 problem / cause / fix / docs_url）
- 穩定 UUID，跨呼叫可組合
- 非同步任務（i2v）支援 polling 與 webhook

**F-51. MCP Server（Phase 1 M3.5；2026-04-30 從 Phase 2 拉回）**
- agent-first / agent-native / agent-friendly 是 Character Foundry 靈魂；MCP server 從 Phase 2 暫緩拉回 Phase 1 M3.5（M3 ship 後接續），與 OAuth 2.1 配對
- 詳細規劃見 `planning/agent-interface/`、`planning/auth/`

### 4.7 歷史與來源追蹤

**F-60. Generation log**
- 每次 AI 生成（Base / Alias / Motion）都記錄：
  - 使用的 prompt（最終 prompt，含固定 constraints）
  - 模型版本
  - 時間戳
  - 成本（token / generation unit）
  - 成功 / 失敗狀態
- 使用者可在 Character 詳細頁查看完整 log

---

## 5. Phase 1 不做的（NOT in scope）

- **Lip sync**（嘴型同步）— 暫緩，未驗證過是否 acceptable
- **Image-to-3D**（立體模型生成）— 願景保留但實作暫緩
- **Share link / 公開瀏覽**
- **多 team / 跨 team 協作**
- **即時串流 API / webhook 訂閱**
- **版本控制 / branching**（Base 不可變，沒有 v2/v3 概念）
- **付費 / 計費系統**
- **第三方整合**（Slack 通知、Zapier 等）
- **行動裝置支援**（Phase 1 假設桌面瀏覽器）

---

## 6. Phase 1 平台級基礎建設決策（Locked）

以下 7 項為 Phase 1 定案，所有下游 agent 依此規劃：

| # | 項目 | Phase 1 決定 | 未來升級方向 |
|---|---|---|---|
| B1 | Copy 範圍 | Base + Aliases，**不含 Motions** | 若使用者反映不便再加 |
| B2 | 檔案儲存 | **本機檔案系統**，backend 用 abstract storage interface | 無痛切 S3 / MinIO |
| B3 | 部署目標 | **內網自架 server** | 規模擴大後上雲 |
| B4 | 認證機制 | Phase 1 起步：**簡單帳密 + JWT**（access 15min, refresh 30d）。**M3.5 升級為 OAuth 2.1 + PKCE**（human auth code+PKCE / agent client credentials），dual-stack migration。詳見 `../auth/`。 | OAuth scope expansion / SSO 後續評估 |
| B5 | Team 模型 | **單一 team**（schema 保留 `team_id`）| 多 team |
| B6 | 成本控管 | **軟性 quota** — UI 顯示使用量，不硬擋 | 硬性 quota / admin approval |
| B7 | 語言策略 | **UI 中文 + Prompt 英文**（由 LLM reconciler 翻譯注入）| 雙語 UI |

## 7. 平台級固定 constraints（所有生成皆注入）

這些**永遠**存在於最終 prompt，無法被使用者關閉：

- Transparent background
- Character centered in frame
- Character facing camera directly（正面）
- Full body（除非選單明確選「頭像 only」）
- Consistent lighting neutral / soft

細節 prompt 文案由 Backend Agent 在 prompt template 階段撰寫。

## 8. 語言策略（B7 展開）

- **UI 語言**：中文（繁體）
- **使用者輸入**：接受中文（選單選項本身是中文標籤，自由補述使用者用中文寫）
- **送 AI 模型的 prompt**：**英文**
- **翻譯層**：Prompt Reconciler LLM
  - 輸入：中文補述 + 平台固定 constraints + 選單選項對應 fragments
  - 職責：
    1. 把中文補述翻成英文
    2. 解決使用者補述與平台 constraints 的衝突（重寫補述使其與 constraints 相容）
    3. 組合成最終英文 prompt
  - 實作：Backend Agent 決定具體用哪個 LLM（可能 GPT / Claude / 其他）
- **例外**：使用者命名（Character name、Alias name、Motion name）**保留原文**，不翻譯

## 9. 關聯文件

- `data-model.md` — 完整資料模型
- `open-questions.md` — 尚待決定的問題清單
- `../project-brief.md` — 專案背景
- `../../ideas.md` — 平台目標與流程總覽
