# Character Foundry — Open Questions

> **Status:** Living document · 2026-04-23
> **Owner:** Product Agent
> **Purpose:** 追蹤尚未決定的產品問題，並記錄已決定項目的決議內容

---

## ✅ Blockers — 全部 Locked（2026-04-23）

### B1. ✅ Copy Character 的複製範圍

**決議：** **Base + Aliases，不含 Motions**

原因：Motion 檔案量大且每個使用者的動作需求不同，複製者自己按需生成即可。

**落地於：** `functional-scope.md` F-41、`data-model.md` Character.copied_from_character_id

---

### B2. ✅ 檔案儲存位置

**決議：** **Phase 1 本機檔案系統**。Backend 用 abstract storage interface（例：Storage trait / adapter pattern），之後可無痛切 S3 / MinIO。

**儲存路徑約定：**
```
{STORAGE_ROOT}/
├── characters/{character_id}/
│   ├── base.png
│   ├── aliases/{alias_id}.png
│   └── motions/{motion_id}.mp4
└── exports/{character_id}-{timestamp}.zip
```

**落地於：** `data-model.md` §4

**給 DevOps Agent 的暗示：** `STORAGE_ROOT` 是環境變數，部署時指向一個持久化 volume / 磁碟分割。

---

### B3. ✅ 部署目標

**決議：** **Phase 1 內網自架 server**（單機或小 VM）。

規模擴大後上雲的路徑保留：backend 所有外部依賴（DB、檔案儲存、AI API endpoint）都走環境變數 + interface，無硬編碼。

**落地於：** DevOps Agent 依此規劃 Docker / docker-compose 或裸機部署。

---

### B4. ✅ 認證機制

**決議：** **簡單帳密 + JWT**

- 後端提供 `/auth/login` 與 `/auth/register`（Phase 1 註冊可能是 admin-only）
- JWT 放 `Authorization: Bearer <token>` header
- Token 有效期 + refresh token 機制
- 密碼以 bcrypt 或 argon2 hash

**落地於：** `data-model.md` User.password_hash、`functional-scope.md` §6 表格

**給 Backend Agent：** auth middleware 要過濾每個 API 路徑（除了 /auth/login, /auth/register, /health）。

---

### B5. ✅ 單一 team 還是多 team

**決議：** **Phase 1 單一 team**，schema 保留 `team_id` 欄位。

- Bootstrap 時建立一筆 default team
- 所有 User 的 `team_id` 指向該 default team
- UI 不顯示 team 切換
- 未來加多 team：新增 Team 記錄 + 提供 team 切換 UI，schema 不用改

**落地於：** `data-model.md` §2.1、`functional-scope.md` F-42

---

### B6. ✅ 成本控管

**決議：** **軟性 quota**

- 每次 AI 生成記錄成本於 `GenerationLog.cost_units`
- UI 頂部導覽列顯示累積使用量（該月 / 累積）
- 不硬擋

**落地於：** `data-model.md` §6 `UserUsageSummary`

**給 Frontend Agent：** 需要設計一個 usage dashboard component（小 widget）。

---

### B7. ✅ 語言策略

**決議：** **UI 中文（繁體）+ Prompt 英文**

- UI 全部繁體中文
- 使用者輸入（補述、命名）接受中文
- 送 AI 模型的 prompt 全部英文
- **翻譯由 Prompt Reconciler LLM 統一處理**（同時處理衝突 reconciliation）
- 命名（Character name / Alias name / Motion name）保留原文，不翻譯

**落地於：** `functional-scope.md` §8、F-04a

**給 Backend Agent：** Prompt Reconciler 是獨立模組，要可單獨測試，輸入 = {中文補述, 平台 constraints, 選單 fragments}，輸出 = 英文最終 prompt。

---

## ✅ Medium 已決定項目（2026-04-23）

### M1. ✅ 自由補述 vs 平台 constraints 衝突處理

**決議：** **用一層 LLM（Prompt Reconciler）重寫使用者補述**，使其與平台 constraints 相容，同時順便做中英翻譯。

**落地於：** `functional-scope.md` F-04a、§8

---

### M2. ✅ 最終 prompt 是否顯示給使用者

**決議：** **預設不顯示，提供「進階檢視」按鈕展開查看**

可展開查看：
- 平台固定 constraints
- LLM reconciler 處理後的補述
- 完整最終 prompt

使用者**無法直接編輯**最終 prompt。

**落地於：** `functional-scope.md` F-04b

---

### M3. ✅ Alias 生成的輸入介面

**決議：** **一律從 Base 當底圖**，接受三種輸入（可單獨或混合）：
1. 文字補述
2. 參考圖上傳
3. Inpaint 區域標記

**落地於：** `functional-scope.md` F-10

**給 UX Agent：** Alias 建立頁需要設計三種輸入並存的 UI — 最可能是 tabs 或 toggle。Inpaint 區域標記需要互動式圖片編輯器。

---

### M4. ✅ 自訂 Motion 輸入方式

**決議：** **Phase 1 僅純文字輸入** — 欄位：
- 動作名稱（必填）
- 動作描述（必填，prompt 格式）

不做參考影片上傳、不做選單。

**落地於：** `functional-scope.md` F-21

---

## 🟡 Medium — 仍開放

### M5. 🟡 Dropdown 選項的實際內容

**問題：** 性別、眼型、鼻型、髮型、風格...每個 dropdown 要有哪些選項？每個選項對應什麼 prompt fragment？

**Deferred to:** 後續內容填充階段。**不影響架構設計，可平行進行**。Product Agent 與 UX Agent 協作決定。

**初步建議：** 先用最小集合 ship（性別 2 選 + 眼型 5 選 + 髮型 5 選 + 風格 3 選），後續擴充。

---

### M6. ✅ Character / Alias / Motion 命名規則

**決議（2026-04-23）：**
- Character name：**1-50 字元**，同 `owner_id` 下唯一
- Alias name：同 `character_id` 下唯一
- Motion name：同 `parent_id`（Base 或 Alias）下唯一
- 允許字元：中文、英數字、底線、連字號（禁止 emoji、特殊符號）
- 自動產生 URL-safe slug（pinyin / 英數轉換，衝突時後綴 `-2`, `-3`）

**落地於：** `data-model.md` §4

---

### M7. 🟡 錯誤處理 UX

**問題：** AI 生成失敗（timeout / content policy / API error）時 UI 顯示什麼？使用者能重試幾次？

**Deferred to:** UX + Backend Agent。需要對照 AI 模型實際失敗模式設計。

**PM 註記：** 由於平台是 agent-friendly 設計，錯誤格式要結構化（`AgentError { code, problem, cause, fix, docs_url }`），UI 層把結構化錯誤包成人類可讀訊息。

---

### M8. 🟡 Lip sync 未驗證的賭注

**問題：** 延後 lip sync 是未經驗證的決定。第一次 demo 給實際使用者看時可能會被打臉。

**Action:** 記錄為已知風險。Phase 1 ship 前建議做一次內部 demo 驗證（例：拿一段 Seedance 輸出 + TTS 疊加，放給 5 位不相關的人看）。

---

## 標記約定

- ✅ Decided — 已決定，寫入相關 spec
- 🔴 Blocker（本文件目前為空）— 其他 agent 的規劃會因此停滯
- 🟡 Medium — 可在對應 agent 進場時決定，不影響前期規劃

---

## 給下一個 Agent 的建議順序

1. **Data Agent** 先進場 — 基於 `data-model.md` 定 schema，回答 §7 的細節問題
2. **Backend Agent** 進場 — 定 API 清單、prompt reconciler 設計、storage abstraction、auth middleware
3. **UX Agent** 進場 — 根據 functional-scope 畫 flow 與 wireframe，解決 M3 / M7
4. **Frontend Agent** 進場 — 基於 UX 產出 React 架構與元件拆解
5. **DevOps Agent** 進場 — 部署計畫、CI/CD、secret 管理

Backend 與 UX 可適度**平行** — backend 定 API shape 時不用等 UX 細節，UX 畫 wireframe 時也不用等 API 實作。

---

## 關聯文件

- `functional-scope.md`
- `data-model.md`
- `../project-brief.md`
