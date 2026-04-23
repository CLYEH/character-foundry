# Character Foundry — 資料模型（Data Model）

> **Status:** Draft v0.1 · 2026-04-23
> **Owner:** Product Agent
> **Note:** 這是**產品層**的資料模型（邏輯結構），不是資料庫 schema。Data Agent 會基於此設計 DB schema。

---

## 1. 實體關係圖（ER Overview）

```
┌──────────┐       ┌──────────┐
│   Team   │──1:N──│   User   │
└──────────┘       └────┬─────┘
                        │ owns
                        ▼
                  ┌──────────┐        ┌─────────────────────┐
                  │Character │──1:1──▶│  Creation Session   │
                  └────┬─────┘        └──────┬──────────────┘
                       │                      │
                       │                      └──1:N──▶ Checkpoint
                       │
                       ├──1:1──▶ Base ──1:N──▶ Motion
                       │
                       └──1:N──▶ Alias ──1:N──▶ Motion

                  Every AI gen ──logs──▶ GenerationLog
```

---

## 2. 實體定義

### 2.1 `Team`

**Phase 1 決策（B5）：單一 team / workspace**。Schema 保留欄位，所有 User 指向同一筆 Team 記錄。之後加 multi-team 不用動 schema。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | UUID | 主鍵 |
| `name` | string | Team 名稱（Phase 1 固定一個，例：「default」）|
| `created_at` | timestamp | 建立時間 |

### 2.2 `User`

團隊成員。Phase 1 認證為簡單帳密 + JWT（B4）。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | UUID | 主鍵 |
| `team_id` | UUID FK → Team | 所屬 team |
| `name` | string | 顯示名稱 |
| `email` | string, unique | 登入識別 |
| `password_hash` | string | bcrypt / argon2 | 
| `created_at` | timestamp | 建立時間 |
| `last_login_at` | timestamp, nullable | 最後登入 |

### 2.3 `Character`

最上層的角色容器。**使用者面對的主要單位**。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | UUID | 主鍵（穩定、跨系統可用）|
| `team_id` | UUID FK → Team | 所屬 team |
| `owner_id` | UUID FK → User | 建立者（僅 owner 可編輯）|
| `name` | string | 使用者命名（必填，1-50 字元，同 `owner_id` 下唯一）|
| `slug` | string | URL-safe 識別字串（系統從 name 自動生成，同 `owner_id` 下唯一，衝突時後綴 `-2`, `-3`）|
| `base_id` | UUID FK → Base | 已確立的 base（鎖死後不變）|
| `creation_session_id` | UUID FK → CreationSession | 建立過程的記錄 |
| `copied_from_character_id` | UUID FK → Character, nullable | 若為 copy 而來，指向來源（Copy 範圍：Base + Aliases，不含 Motions，B1）|
| `created_at` | timestamp | 建立時間 |
| `updated_at` | timestamp | 最後修改時間（指 alias/motion 的變動）|

**性質：**
- Character 本身 mutable（可加/刪 aliases、可加/刪 motions、可改名）
- `base_id` 一旦設定**不可修改**（建立流程完成後鎖死）

### 2.4 `CreationSession`

建立 Character 過程的臨時容器。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | UUID | 主鍵 |
| `character_id` | UUID FK → Character, nullable | 若 session 成功完成，關聯到產生的 Character；尚未完成則 null |
| `initiator_id` | UUID FK → User | 發起建立的使用者 |
| `input_mode` | enum | `template` / `reference` |
| `status` | enum | `in_progress` / `completed` / `abandoned` |
| `created_at` | timestamp | 開始時間 |
| `completed_at` | timestamp, nullable | 確立 Base 的時間 |

### 2.5 `Checkpoint`

Creation session 中每次生成的候選圖。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | UUID | 主鍵（穩定，可跨 Character 引用作為建立起點）|
| `creation_session_id` | UUID FK → CreationSession | 所屬 session |
| `sequence` | int | 在 session 中的順序 |
| `prompt` | text | 最終送模型的完整 prompt（含平台 constraints）|
| `user_menu_selections` | JSON | 模式 A 的選單選項原始值 |
| `user_freeform_note` | text | 使用者補述原文 |
| `reference_images` | array of URLs | 若為模式 B，使用的參考圖 |
| `seed` | string | 模型 seed（若模型支援）|
| `output_image_url` | URL | 產出圖片的儲存路徑 |
| `generation_log_id` | UUID FK → GenerationLog | 對應的生成紀錄 |
| `selected_as_base` | boolean | 是否最終被選為 Character 的 Base |
| `created_at` | timestamp | 生成時間 |

**性質：**
- Immutable（一旦生成不修改）
- 即使 session 完成後也保留（可作為新 Character 的起點）
- 當使用者從另一 Checkpoint 開新 Character 時，會建立新的 CreationSession，並把該 checkpoint 作為起始輸入

### 2.6 `Base`

Character 的「最素的模樣」。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | UUID | 主鍵 |
| `character_id` | UUID FK → Character | 1:1 關係 |
| `from_checkpoint_id` | UUID FK → Checkpoint | 來自哪個 checkpoint |
| `image_url` | URL | 圖片儲存路徑 |
| `created_at` | timestamp | 確立時間 |

**性質：**
- **Immutable**。確立後不修改、不刪除（除非整個 Character 刪除）
- 所有 AI model 版本、prompt 資訊透過 `from_checkpoint_id` 可追溯

### 2.7 `Alias`

Character 的變體（不同衣裝 / 配件 / 場景）。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | UUID | 主鍵 |
| `character_id` | UUID FK → Character | 所屬 Character |
| `name` | string | 使用者命名（例：「紅旗袍版」）|
| `prompt` | text | 用於生成 Alias 的 prompt |
| `user_freeform_note` | text | 使用者補述 |
| `input_mode` | enum | `image2image` / `inpaint` / `text2image` |
| `mask_data` | JSON, nullable | 若用 inpaint，記錄 mask 區域 |
| `image_url` | URL | Alias 圖片路徑 |
| `generation_log_id` | UUID FK → GenerationLog | 對應的生成紀錄 |
| `created_at` | timestamp | 建立時間 |

**性質：**
- Mutable name（使用者可改名）
- Image 本身 immutable（要改 = 新增另一個 Alias）
- 平行並列於同一 Character 底下，無階層

### 2.8 `Motion`

動作影片。掛在 Base 或某個 Alias 底下。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | UUID | 主鍵 |
| `parent_type` | enum | `base` / `alias` |
| `parent_id` | UUID | 指向 Base.id 或 Alias.id |
| `motion_type` | enum | `preset_wave` / `preset_nod` / `preset_gesture` / `preset_happy` / `preset_idle` / `custom` |
| `name` | string | Phase 1：preset 用預設名，custom 由使用者命名 |
| `description` | text | Custom motion 的 prompt 描述 |
| `video_url` | URL | 影片路徑（.mp4）|
| `duration_ms` | int | 影片長度 |
| `generation_log_id` | UUID FK → GenerationLog | 對應的生成紀錄 |
| `created_at` | timestamp | 建立時間 |

**性質：**
- Preset 類型有 5 種固定，`motion_type` enum 限定
- Custom 類型 `motion_type = custom`，使用者可在 Phase 1 任何時候新增
- 每個 Motion 只屬於一個 parent（Base 或 Alias），不共享

### 2.9 `GenerationLog`

所有 AI 生成的審計紀錄。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | UUID | 主鍵 |
| `user_id` | UUID FK → User | 觸發者 |
| `character_id` | UUID FK → Character, nullable | 關聯的 Character（checkpoints 可能還沒有）|
| `entity_type` | enum | `checkpoint` / `alias` / `motion` |
| `entity_id` | UUID | 對應實體 ID |
| `model_name` | string | 例：`gpt-image-2`、`seedance-2.0` |
| `model_version` | string | 具體版本號 |
| `final_prompt` | text | 實際送到模型的 prompt（含所有 constraints）|
| `input_images` | array of URLs | 若有輸入圖 |
| `parameters` | JSON | seed / temperature / etc. |
| `cost_units` | decimal | 成本（token 或 generation unit）|
| `status` | enum | `success` / `failed` / `timeout` |
| `error_message` | text, nullable | 失敗時的錯誤訊息 |
| `duration_ms` | int | 生成耗時 |
| `started_at` | timestamp | 開始時間 |
| `completed_at` | timestamp, nullable | 完成時間 |

---

## 3. 匯出 ZIP 的 `manifest.json` 結構

每個下載 ZIP 內含一個 `manifest.json`，格式：

```json
{
  "manifest_version": "1.0",
  "exported_at": "2026-04-23T10:00:00Z",
  "character": {
    "id": "uuid-xxx",
    "name": "古風導覽員-小雅",
    "created_at": "2026-04-20T08:15:00Z",
    "owner": {
      "id": "uuid-user",
      "name": "Alice"
    }
  },
  "base": {
    "id": "uuid-base",
    "file": "base/base.png",
    "generation": {
      "model": "gpt-image-2",
      "model_version": "v1.0",
      "prompt": "...",
      "input_mode": "template",
      "menu_selections": { ... }
    }
  },
  "aliases": [
    {
      "id": "uuid-alias-1",
      "name": "紅旗袍版",
      "file": "aliases/uuid-alias-1.png",
      "generation": { ... }
    }
  ],
  "motions": [
    {
      "id": "uuid-motion-1",
      "parent": { "type": "base", "id": "uuid-base" },
      "motion_type": "preset_wave",
      "name": "招手歡迎",
      "file": "motions/uuid-motion-1.mp4",
      "duration_ms": 3500,
      "generation": { ... }
    },
    {
      "id": "uuid-motion-2",
      "parent": { "type": "alias", "id": "uuid-alias-1" },
      "motion_type": "custom",
      "name": "轉身揮手",
      "description": "慢慢轉身，揮手打招呼",
      "file": "motions/alias-uuid-alias-1/uuid-motion-2.mp4",
      "duration_ms": 5200,
      "generation": { ... }
    }
  ]
}
```

**規則：**
- 所有 ID 與平台上的 UUID 一致（下游可跨系統引用）
- `file` 路徑是 ZIP 內相對路徑
- `generation` 物件完整保留生成時的模型、prompt、參數（可追溯）

---

## 4. 識別與命名規則

- **所有 entity ID 一律使用 UUID v4**（避免順序猜測 + 跨系統唯一）
- **檔名格式**：`{uuid}.{ext}`（不使用使用者自訂檔名）
- **Character 名稱**（M6 Locked 2026-04-23）：
  - 長度限制：**1-50 字元**
  - 同 `owner_id` 下唯一（不同 owner 可以有同名角色）
  - 自動產生 URL-safe `slug`（pinyin / 英數轉換，衝突時後綴 `-2`, `-3`）
  - 允許字元：中文、英數字、底線、連字號（禁止 emoji、特殊符號）
- **Alias 名稱**：同 `character_id` 下唯一（一個 Character 不能有兩個「紅旗袍版」）
- **Motion 名稱**：同 `parent_id` 下唯一（一個 Base 或 Alias 不能有兩個同名動作）
- **檔案儲存路徑（B2 Phase 1 本機檔案系統）**：
  ```
  {STORAGE_ROOT}/
  ├── characters/{character_id}/
  │   ├── base.png
  │   ├── aliases/{alias_id}.png
  │   └── motions/{motion_id}.mp4
  └── exports/{character_id}-{timestamp}.zip
  ```
  Backend 用 abstract storage interface，之後可換 S3 / MinIO 不影響上層

---

## 5. 不可變性摘要

| Entity | 可變欄位 | 不可變欄位 |
|---|---|---|
| Character | `name`, aliases/motions 增刪 | `id`, `base_id`（確立後）, `team_id`, `owner_id` |
| Base | 無 | **所有欄位**（完全不可變）|
| Alias | `name` | 圖片本身、`prompt` |
| Motion | `name`（custom 可改）| 影片本身、`parent` |
| Checkpoint | 無 | **所有欄位** |
| GenerationLog | 無 | **所有欄位**（審計紀錄）|

---

## 6. 額外資料：成本追蹤（B6 軟性 quota）

每次 AI 生成會寫入 `GenerationLog`（§2.9），`cost_units` 欄位累加。

建議 Data Agent 新增：

### `UserUsageSummary`（materialized view 或 cache）

| 欄位 | 型別 | 說明 |
|---|---|---|
| `user_id` | UUID FK | 使用者 |
| `period` | string | `month-2026-04` 等粒度 |
| `image_gen_count` | int | gpt-image-2 呼叫次數 |
| `video_gen_count` | int | Seedance 2.0 呼叫次數 |
| `cost_units_total` | decimal | 累積成本 |
| `updated_at` | timestamp | 最近更新 |

UI 在頂部導覽列顯示當月累積成本，不硬擋但讓使用者有感（B6）。

## 7. 尚待 Data Agent 確認的細節

- 唯一性 scope 索引：`(owner_id, name)` UNIQUE，`(owner_id, slug)` UNIQUE（Phase 1 單 team，不需要 team_id 參與唯一性鍵）
- Slug 演算法：建議 pinyin 轉換 + 英數保留 + 衝突後綴（具體由 Data Agent 決定 library）
- `GenerationLog` 是否要 partition（按月）？量會大
- 檔案路徑是否含使用者維度（`users/{id}/characters/...`）？單 team 下可能不需要，但若之後 multi-team 會需要

---

## 8. 關聯文件

- `functional-scope.md` — 功能範圍
- `open-questions.md` — 待決定問題
- `../data/CLAUDE.md` — Data Agent 角色定位（接下來會基於此設計 DB schema）
