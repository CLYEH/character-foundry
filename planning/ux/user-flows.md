# Character Foundry — User Flows & Page Inventory

> **Status:** Draft v0.1 · 2026-04-23
> **Owner:** UX Agent
> **Based on:** `../product/functional-scope.md`, `../backend/api-shape.md`

---

## 1. 設計原則

1. **非同步任務永不阻塞**：任何 AI 生成不卡住 UI，使用者可繼續操作其他東西
2. **生成中的 feedback 要有空間感**：不只是 spinner，要讓使用者知道「大概還要多久」
3. **錯誤要可行動**：不只顯示「失敗」，告訴使用者下一步可以做什麼
4. **Agent-friendly 對應的 UX**：所有 UI 操作都對應清楚的 API call；沒有「UI 才懂的暗黑魔法」
5. **團隊感但保持空間**：看得到別人的 Character，但不會誤改別人的

---

## 2. Page Inventory（Phase 1）

| # | 頁面 | 路徑 | 說明 |
|---|---|---|---|
| P-01 | 登入 | `/login` | Email + password |
| P-02 | Dashboard | `/` | Character grid + 快速建立入口 |
| P-03 | 新增 Character | `/characters/new` | 選輸入模式（template / reference）|
| P-04 | Creation Session | `/characters/new/session/{session_id}` | Checkpoints 迭代介面 |
| P-05 | Character 詳細 | `/characters/{slug}` | Base + Aliases + Motions 總覽 |
| P-06 | Alias 編輯 | `/characters/{slug}/aliases/new` | 新增 alias（三合一輸入）|
| P-07 | 使用量 | `/usage` | 使用量 dashboard |
| P-08 | 設定 | `/settings` | 個人資料、password 修改 |

**Modal / Overlay：**
- M-01 進階 prompt 檢視
- M-02 自訂 Motion 輸入對話框
- M-03 Copy 確認對話框
- M-04 刪除 / 還原確認對話框
- M-05 ZIP 匯出進度對話框

**全域元素：**
- Top Nav（logo / search / usage widget / user menu）
- Toast notification（task 完成 / 失敗）
- 離線 / 網路錯誤 banner

---

## 3. Navigation Model

```
                   ┌─────────────────────────────────┐
                   │  Top Nav（所有頁面固定）          │
                   │  Logo | Search | Usage | User    │
                   └─────────────────────────────────┘
                              │
                              ▼
                         /  (Dashboard)
                          │
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
     New Character  Character Detail   Usage / Settings
          │               │
          ▼               ├──▶ Add Alias
    Creation Session      ├──▶ Generate Motion
          │               ├──▶ Download ZIP
          ▼               └──▶ Copy
    Character Detail
```

---

## 4. 核心 User Flows

### 4.1 Flow A：建立第一個 Character（Template 模式）

```
┌──────────────┐
│ Dashboard    │ Empty state："還沒有角色，來建一個吧"
│ (P-02)       │ → CTA「建立 Character」
└──────┬───────┘
       │ [建立 Character]
       ▼
┌──────────────────────────────┐
│ 新增 Character (P-03)        │
│ ┌──────────────────────────┐ │
│ │ Character 名稱：___       │ │
│ │ [ Template ] [ Reference ] │  ← 兩個大選項
│ └──────────────────────────┘ │
└──────┬───────────────────────┘
       │ [選 Template + 填名]
       │  API: POST /characters
       ▼
┌────────────────────────────────────────────────┐
│ Creation Session (P-04)                         │
│                                                 │
│ 左欄：輸入控制                左欄：Checkpoints │
│ ┌────────────┐              ┌────────┐ ┌────────┐│
│ │ 性別 ▼      │              │ Ckpt 1 │ │ Ckpt 2 ││
│ │ 眼型 ▼      │              │ [圖]   │ │ [圖]   ││
│ │ 髮型 ▼      │              │ [用這張]│ │ [用這張]││
│ │ ...         │              └────────┘ └────────┘│
│ │ [補述]      │                                   │
│ │ ____________│                                   │
│ │             │              ┌────────┐           │
│ │ [生成]      │              │ Ckpt 3 │           │
│ │ [重試]      │              │ [圖]   │           │
│ │ [進階檢視]  │              │ [選作 Base] │      │
│ └────────────┘              └────────┘           │
└─────┬──────────────────────────────────────────┘
      │ [生成] → task → SSE stream
      │ [使用者看結果，決定]：
      │   a. 點「重試」→ 同 prompt 再一張
      │   b. 點某 checkpoint「用這張再改」→ 預填該 ckpt 輸入，改後生
      │   c. 點「從頭」→ 清空輸入
      │   d. 滿意 → 點某 checkpoint「選作 Base」
      ▼
┌──────────────────────────────┐
│ Character 詳細 (P-05)         │
│ Base 確立，Character 建成      │
└──────────────────────────────┘
```

**關鍵 UX 決定：**
- Checkpoints **不刪除**，全部保留（右欄可捲動）
- 每個 checkpoint 顯示 **序號** + **縮圖** + **時間戳**
- 點縮圖 → 全螢幕 lightbox
- Lightbox 可看該 checkpoint 的 prompt（透過 `/v1/prompt/preview` 或直接從 task result 帶）

### 4.2 Flow B：新增 Alias（三合一輸入）

**這裡回答 PM 的 M3 open question。**

```
┌──────────────────────────────┐
│ Character 詳細 (P-05)         │
│ Base 旁邊「+ 新增 Alias」按鈕 │
└──────┬───────────────────────┘
       │ [+ 新增 Alias]
       ▼
┌──────────────────────────────────────────────────────┐
│ Alias 編輯 (P-06)                                     │
│                                                        │
│ ┌────────────────────┐  ┌────────────────────────┐  │
│ │ Base 圖（互動）      │  │ Alias 名稱：______       │  │
│ │                    │  │                          │  │
│ │   [base.png]       │  │ 輸入方式（可混用）：     │  │
│ │                    │  │                          │  │
│ │ [啟用 Inpaint]      │  │ ☑ 文字補述               │  │
│ │ ↳ 使用者可在圖上    │  │ ┌──────────────────┐   │  │
│ │   拖曳畫 mask       │  │ │ 換成紅色旗袍...     │   │  │
│ │                    │  │ └──────────────────┘   │  │
│ │ [清除 mask]         │  │                          │  │
│ │                    │  │ ☐ 參考圖                  │  │
│ └────────────────────┘  │ [上傳圖片] or 拖放       │  │
│                          │                          │  │
│                          │ [進階檢視] [生成]        │  │
│                          └────────────────────────┘  │
└──────────────────────────────────────────────────────┘
```

**互動規則：**
- Base 圖預設「只看」模式
- 點「啟用 Inpaint」→ 進入互動 mask 繪製模式（支援 brush / eraser / 清除）
- 至少要填一項（補述、參考圖、mask 三選一或多）
- 否則「生成」按鈕 disabled
- Inpaint mask 傳 PNG bitmap（詳見 §6）
- **Alias 永遠從 Base 生**，不能基於另一個 alias（M3 決議）

**生成過程：**
- 按下「生成」→ 頁面不離開，loading 狀態置入（spinner + 預估時間）
- 任務進入 queue → SSE 即時更新
- 完成 → Alias 加入 Character detail 的 alias 列表，該頁面 close（自動 nav 回 P-05）

### 4.3 Flow C：生成 Motion（預設 + 自訂）

```
┌──────────────────────────────┐
│ Character 詳細 (P-05)         │
│                              │
│ Base 卡片：                    │
│  ┌──────────────────┐        │
│  │ [base.png]       │        │
│  │                  │        │
│  │ Motions：         │        │
│  │ ┌───┐ ┌───┐ ┌───┐│        │
│  │ │🎬 │ │+  │ │+  ││  ← 5 個預設固定位置 + 「+」可加自訂
│  │ │招手│ │點頭│ │手勢││
│  │ └───┘ └───┘ └───┘│
│  │      已生成 1/5  │        │
│  │                  │        │
│  │ [+ 自訂動作]     │        │
│  └──────────────────┘        │
│                              │
│ Alias 卡片：                  │
│  (相同結構)                  │
└──────────────────────────────┘
```

**UX 規則：**
- 預設動作有 **5 個固定位置**，點 `[+]` 圖示觸發生成該類型
- 已生成的用影片縮圖顯示，點擊 → lightbox 播放
- 狀態：`+` 未生成 → `🕒` 生成中（spinner 覆蓋）→ `🎬` 已完成 → 錯誤則顯示「!」
- 自訂動作透過 **Modal M-02** 輸入（name + description）

**Modal M-02 自訂 Motion 對話框：**

```
┌────────────────────────────────┐
│ 新增自訂 Motion            [x] │
├────────────────────────────────┤
│                                │
│ 動作名稱 *                      │
│ ┌──────────────────────────┐ │
│ │ 轉身揮手                    │ │
│ └──────────────────────────┘ │
│                                │
│ 動作描述 *                      │
│ ┌──────────────────────────┐ │
│ │ 慢慢轉身 180 度，然後揮手  │ │
│ │ 打招呼                      │ │
│ └──────────────────────────┘ │
│                                │
│ [取消]                [生成]    │
└────────────────────────────────┘
```

### 4.4 Flow D：Copy 他人 Character

```
┌──────────────────────────────┐
│ Dashboard (P-02)              │
│ Alice 的 Character 卡片：      │
│  ┌────────────┐              │
│  │ [Alice角色]│              │
│  │ 紅旗袍導覽員│              │
│  │ by Alice    │              │
│  │ [Copy]      │  ← 非 owner 看到 [Copy] 按鈕
│  └────────────┘              │
└──────┬───────────────────────┘
       │ [Copy]
       ▼
┌────────────────────────────────┐
│ Modal M-03 Copy 確認            │
│ 「要複製 Alice 的『紅旗袍...』？│
│  Base + Aliases 會被複製        │
│  Motions 需要你重新生成」       │
│ 新角色名稱：______              │
│ [取消] [確認複製]               │
└──────┬─────────────────────────┘
       │ [確認]
       │ API: POST /characters/{id}/copy
       │ → Task
       ▼
┌──────────────────────────────┐
│ Toast「Copy 中...」           │
│ (右下角，不阻塞操作)          │
│ 完成後 Toast → "已複製，查看" │
│ 點擊 → 跳該新 Character 頁面  │
└──────────────────────────────┘
```

### 4.5 Flow E：ZIP 下載

```
Character Detail (P-05) → 「下載 ZIP」按鈕
    │
    ▼
API: GET /characters/{id}/export → Task
    │
    ▼
Modal M-05 進度對話框
 ┌────────────────────────────────┐
 │ 打包中...                       │
 │ ▓▓▓▓▓▓▓░░░░░  60%               │
 │ 預計 30 秒                      │
 │ [在背景繼續]   [取消]           │
 └────────────────────────────────┘
    │ 完成
    ▼
Modal 變成「下載」
 ┌────────────────────────────────┐
 │ ✓ 準備完成                      │
 │ character-xxx.zip (85 MB)      │
 │ [立即下載]                      │
 └────────────────────────────────┘
    │
    ▼
Browser 下載（透過 signed URL 302 redirect）
```

---

## 5. 互動狀態規範（Interaction States）

對應 PM 的 M7 open question。

### 5.1 Loading State

| 情境 | UX |
|---|---|
| Page 載入 | Skeleton UI（卡片佔位）|
| 按下「生成」後等 task 建立 | Button 變 disabled + 內嵌 spinner |
| Task queued | 顯示 queue position（"#3 in queue"）|
| Task running | Progress bar（有精細度）或 indeterminate spinner（無精細度）+ 預估剩餘時間 |
| Task partial 結果 | 若 SSE 回傳 partial preview，漸進顯示 |

### 5.2 Success State

| 情境 | UX |
|---|---|
| 一般儲存成功 | Toast（右下，2s 淡出）|
| Task 完成 | Toast「XX 生成完成」+ 點擊跳結果 |
| Export ZIP 完成 | Modal 繼續停留，顯示下載按鈕 |
| 操作成功但需要確認 | Inline message（例：「Base 已確立」）|

### 5.3 Error State（三層）

**Layer 1 — 表單驗證（inline）：**

```
Character 名稱 *
┌────────────────────────────┐
│ Alice的角色                  │
└────────────────────────────┘
⚠ 你已有一個同名角色
```

Backend 回 `VALIDATION_*` / `CONFLICT_*` → 用 `AgentError.message` 顯示。

**Layer 2 — Task 失敗（toast）：**

```
┌──────────────────────────────────┐
│ ✗ Alias「紅旗袍版」生成失敗      │
│                                   │
│ 原因：模型內容政策拒絕            │
│ [詳細]   [重試]   [關閉]          │
└──────────────────────────────────┘
```

點「詳細」→ expand 顯示 `problem` + `cause` + `fix` + `request_id`。

**Layer 3 — 嚴重錯誤（page-level）：**

```
┌──────────────────────────────────┐
│                                   │
│           ⚠                       │
│    連線不到伺服器                 │
│                                   │
│    [重新整理]   [回首頁]           │
│                                   │
└──────────────────────────────────┘
```

適用 401 / 500 / network error。

### 5.4 Empty State

| 情境 | UX |
|---|---|
| Dashboard 無 Character | 插畫 + 「還沒有角色，建一個吧」+ CTA |
| Character 無 Alias | "Base 是基礎，來加些變體吧"（inline 在 alias 區）|
| Base 無 Motion | 5 個 `+` 顯示類型圖示 + 灰底提示「點擊生成」|
| Search 無結果 | 「沒找到『XX』的角色，換個關鍵字試試」 |

---

## 6. 回 Backend §8 的 6 個 open questions

| # | Backend 問題 | UX 決定 | 理由 |
|---|---|---|---|
| 1 | Character 列表預設排序 | **最近更新 DESC**（`updated_at DESC`）| 使用者期待「剛改的放前面」，符合 Notion/Figma 慣例 |
| 2 | `motions_summary` 欄位 | `{ preset_generated_count: int (0-5), custom_count: int, total_duration_ms: int }` | UI 顯示「已生成 3/5」+ 總秒數 |
| 3 | Inpaint mask 格式 | **PNG bitmap（alpha mask）**，尺寸同 base 圖 | 最通用、多數 AI API 接受、Canvas API 好產出 |
| 4 | `Checkpoint.prompt_summary` 壓縮規則 | `freeform_note` 前 80 字元 + "..."。選單選項拼成「女性・大眼・黑長髮・水墨風」類摘要 | Lightbox 能看完整 prompt，列表用摘要 |
| 5 | SSE event schema | `{ status, progress (0-1), partial_preview_url?, message? }`。Backend 每次狀態變化 push 一次；running 狀態額外每 5s push 進度 | 前端 receive 後可直接 render |
| 6 | Progress 精細度 | **0.0 ~ 1.0 float**。UI rule：`progress >= 0.05` 顯示 progress bar，否則用 indeterminate spinner。gpt-image-2 無 progress → 只用 elapsed time | 提供可靠的統一介面，UI 決定顯示策略 |

---

## 7. 需要 Backend 確認的 UX 發現（step 3 請補）

| # | 項目 | 原因 |
|---|---|---|
| U1 | Task 的 `estimated_duration_ms` 怎麼來 | UI 要顯示「預計 30 秒」，需要後端給一個合理 estimate（依模型 + 歷史平均）|
| U2 | SSE `partial_preview_url`：gpt-image-2 是否支援漸進式預覽？ | 若否，SSE 只有狀態變化 |
| U3 | Queue position 是否可查 | 若 queue 會塞車，UI 要顯示「#3 in queue」 |
| U4 | Task cancel 的即時性 | 使用者按「取消」→ 後端能立刻停？還是等當前 API call 完成？|
| U5 | Copy 操作的細節 task 類型 | UI 要顯示 progress 還是只 spinner？|

---

## 8. 關聯文件

- `../product/functional-scope.md` — 功能定義
- `../backend/api-shape.md` — API 接口（UX 基於此設計）
- `wireframes.md` — 詳細頁面 wireframe（本 session step 4 交付）
- `CLAUDE.md` — UX Agent 角色定位
