# Character Foundry — API Shape (Draft v0.1)

> **Status:** Draft for UX handoff · 2026-04-23
> **Owner:** Backend Agent
> **Scope:** Endpoint 清單 + 非同步語義 + 錯誤格式（實作細節見 backend 後續文件）

---

## 1. 總體設計原則

1. **RESTful + OpenAPI 3.1** 自動生成（FastAPI 原生支援）
2. **單一 API 介面**服務 UI 與 agent（不做 UI 專用後門端點）
3. **全部 AI 生成任務非同步**：REST 起任務 → 回 `task_id` → 用 polling 或 SSE 拿結果
4. **穩定 UUID** 作為資源識別，URL 可組合（`/characters/{id}/aliases/{id}/motions/{id}`）
5. **結構化錯誤**：統一 `AgentError` schema，讓 agent 讀得懂
6. **版本化**：所有 endpoint 以 `/v1` 開頭

Base URL（Phase 1）：`http://{internal-host}/api/v1`

---

## 2. 認證（B4 JWT）

### 2.1 Endpoints

```
POST  /v1/auth/login
  Body: { email, password }
  200:  { access_token, refresh_token, expires_in, user: {...} }
  401:  AgentError

POST  /v1/auth/refresh
  Body: { refresh_token }
  200:  { access_token, expires_in }

POST  /v1/auth/logout
  Header: Authorization: Bearer <token>
  200:  { ok: true }

GET   /v1/auth/me
  Header: Authorization: Bearer <token>
  200:  { user: { id, name, email, team_id, created_at } }
```

所有非 `/auth/*` endpoint **必須帶** `Authorization: Bearer <jwt>`。

### 2.2 Token 政策

- `access_token`：15 分鐘
- `refresh_token`：30 天
- Refresh token 存 DB（可 revoke）

---

## 3. 非同步任務語義

### 3.1 生命週期

```
POST /v1/characters/...  (start generation)
    └──▶ 200 Accepted
         { task_id, character_id, status: 'queued', estimated_duration_ms }

GET /v1/tasks/{task_id}  (poll)
    └──▶ 200 OK
         { task_id, status: 'queued' | 'running' | 'completed' | 'failed', ...}

GET /v1/tasks/{task_id}/stream  (SSE)
    └──▶ 200 OK, text/event-stream
         data: {"status":"queued","queue_position":3}
         data: {"status":"queued","queue_position":2}
         data: {"status":"running","progress":0.3}
         data: {"status":"running","progress":0.7}
         data: {"status":"completed","result":{...}}

    SSE event schema：
      {
        status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled',
        queue_position?: number,         // queued 狀態時有值，位置變化時推送
        progress?: number (0..1),         // running 狀態時有值
        partial_preview_url?: string,     // Phase 1 保留 schema 但不填值
        message?: string,                 // 給 UI 顯示的額外訊息
        result?: {...},                   // completed 時含 entity DTO
        error?: AgentError                // failed 時含錯誤物件
      }

    推送時機：
      - status 變化時必推
      - queued: queue_position 變化時推（或每 5s 推一次確認仍在排隊）
      - running: 估算 progress 變化時推（每 2-5s）
      - 終止狀態推送完後 backend 關閉連線
```

### 3.2 Task 狀態

| Status | 意義 |
|---|---|
| `queued` | 已接受，等 worker pick up |
| `running` | Worker 執行中 |
| `completed` | 完成，`result` 欄位含 entity（Checkpoint / Alias / Motion）|
| `failed` | 失敗，`error` 欄位含 AgentError |
| `cancelled` | 使用者取消 |

### 3.3 Task 存活

- `completed` / `failed` 狀態保留 **24 小時**
- `cancelled` 立即可被清除
- 超時 30 分鐘未進入 `running` 的 `queued` task → 自動 `failed` with `TIMEOUT_QUEUE`

### 3.4 Webhook 通知（agent 用）

```
POST /v1/webhooks
  Body: { url, events: ['task.completed', 'task.failed'], secret }
  201:  { webhook_id, ... }

每次 task 狀態變化時，backend 發 POST 到 url：
  Body: { event, task_id, task: {...}, signature: HMAC-SHA256(secret, body) }
  Agent 端驗證 signature 後處理
```

Phase 1 UI 用 polling + SSE；agent 推薦用 webhook（避免長連線）。

---

## 4. 錯誤格式（AgentError）

所有錯誤回應統一格式：

```json
{
  "error": {
    "code": "PROMPT_CONFLICT",
    "message": "使用者補述與平台 constraints 衝突，已自動修正",
    "problem": "Freeform note specified 'cluttered market background' but platform requires transparent background.",
    "cause": "User input conflicts with platform-level image constraints.",
    "fix": "Remove background-related keywords from freeform note, or accept auto-reconciled prompt.",
    "docs_url": "https://docs.internal/character-foundry/errors/PROMPT_CONFLICT",
    "retryable": false,
    "request_id": "req_abc123"
  }
}
```

**欄位說明：**

| 欄位 | 型別 | 說明 |
|---|---|---|
| `code` | string (UPPERCASE_WITH_UNDERSCORES) | 機器可讀錯誤代碼 |
| `message` | string | 面向使用者的訊息（中文，給 UI 顯示）|
| `problem` | string | 發生了什麼（英文，給 agent 讀）|
| `cause` | string | 根本原因 |
| `fix` | string | 如何修正 |
| `docs_url` | URL | 文件連結（Phase 1 可先留 placeholder）|
| `retryable` | boolean | 是否可重試 |
| `request_id` | string | 對應後端 log 的追蹤 ID |

### 4.1 錯誤代碼分類

| Category | Code Prefix | HTTP | 範例 |
|---|---|---|---|
| Auth | `AUTH_` | 401 | `AUTH_INVALID_TOKEN`, `AUTH_EXPIRED`, `AUTH_INSUFFICIENT_PERMISSION` |
| Validation | `VALIDATION_` | 400 | `VALIDATION_NAME_TOO_LONG`, `VALIDATION_INVALID_CHARS` |
| Not Found | `NOT_FOUND_` | 404 | `NOT_FOUND_CHARACTER`, `NOT_FOUND_ALIAS` |
| Conflict | `CONFLICT_` | 409 | `CONFLICT_DUPLICATE_NAME`, `CONFLICT_BASE_LOCKED` |
| Prompt | `PROMPT_` | 400/422 | `PROMPT_CONFLICT`, `PROMPT_CONTENT_POLICY` |
| AI Model | `MODEL_` | 502/504/429 | `MODEL_TIMEOUT`, `MODEL_UNAVAILABLE`, `MODEL_RATE_LIMIT`, `MODEL_QUOTA_EXCEEDED`, `MODEL_INVALID_REQUEST` |
| Storage | `STORAGE_` | 500 / 403 | `STORAGE_WRITE_FAILED`, `STORAGE_NOT_FOUND`, `STORAGE_URL_EXPIRED`（403，retryable=true，Frontend 收到後重新 fetch 含有 signed URL 的父資源）|
| Quota | `QUOTA_` | 402 | `QUOTA_EXCEEDED` (Phase 1 僅警告，不擋) |
| Server | `INTERNAL_` | 500 | `INTERNAL_UNEXPECTED_ERROR` |

---

## 5. Resource Endpoints

### 5.1 Characters

```
GET    /v1/characters
  Query: ?owner_id=me|{user_id}&q={search}&limit=20&cursor={id}
  200:   { items: [Character], next_cursor: string | null }

POST   /v1/characters
  Body:  { name, input_mode: 'template' | 'reference' }
  201:   { character: Character, creation_session: CreationSession }
         (建完 character skeleton 與 session，尚無 base)

GET    /v1/characters/{character_id}
  200:   { character: CharacterDetail }
         含 base、aliases、motions summary

GET    /v1/characters/{character_id}/manifest
  200:   { ...manifest.json format... }
         單獨取 manifest（方便 agent 讀元資料不下圖）

PATCH  /v1/characters/{character_id}
  Body:  { name }
  200:   { character: Character }

DELETE /v1/characters/{character_id}
  204    (soft delete)

POST   /v1/characters/{character_id}/restore
  200:   { character: Character }
         (30 天內可 restore)

POST   /v1/characters/{character_id}/copy
  Body:  { name }  # copy 後的新角色名
  202:   { task_id, new_character_id }
         (B1 範圍：Base + Aliases，不含 Motions)

GET    /v1/characters/{character_id}/export
  202:   { task_id, export_id }

GET    /v1/exports/{export_id}/download
  200:   application/zip (透過 signed URL 302 redirect)
         (7 天有效)
```

### 5.2 Creation Session / Checkpoints

```
GET    /v1/creation-sessions/{session_id}
  200:   { session: CreationSession, checkpoints: [Checkpoint] }

POST   /v1/creation-sessions/{session_id}/checkpoints
  Body:  {
           mode: 'retry_same' | 'remix' | 'fresh',
           base_checkpoint_id: UUID | null,  # 若 mode = remix
           menu_selections: { ... } | null,
           freeform_note: string | null,
           reference_image_ids: [UUID] | null,  # 已上傳的參考圖
           aspect_ratio: 'auto' | '1:1' | '2:3' | '3:2'  # T-047, default '2:3' (直立);
             # 對應 OpenAI gpt-image legal size enum (auto / 1024x1024 /
             # 1024x1536 / 1536x1024)。retry_same / remix 不繼承來源，
             # 吃 request 值；前端 retry_same 按鈕重發 mutation 時會帶
             # 當下 dropdown 值。audit 寫入 generation_logs.parameters
         }
  202:   { task_id, checkpoint_id }
         (非同步，走 task system)

POST   /v1/creation-sessions/{session_id}/reference-images
  Body:  multipart/form-data (file)
  201:   { reference_image_id, url }

POST   /v1/creation-sessions/{session_id}/select-base
  Body:  { checkpoint_id }
  200:   {
           character: Character (updated with base_id),
           base: Base
         }
         (鎖死 base，session 進 completed)

POST   /v1/creation-sessions/{session_id}/abandon
  204    (標記 abandoned，7 天後清理)

POST   /v1/checkpoints/{checkpoint_id}/fork
  Body:  { new_character_name }
  201:   {
           character: Character,
           creation_session: CreationSession
         }
         (從既有 checkpoint 開新 character session)
```

### 5.3 Aliases

```
GET    /v1/characters/{character_id}/aliases
  200:   { items: [Alias] }

POST   /v1/characters/{character_id}/aliases
  Body:  {
           name,
           input_mode: 'text' | 'image' | 'inpaint' | 'mixed',
           freeform_note: string | null,
           reference_image_ids: [UUID] | null,
           mask: { ...inpaint mask... } | null
         }
  202:   { task_id, alias_id }

GET    /v1/aliases/{alias_id}
  200:   { alias: AliasDetail }

PATCH  /v1/aliases/{alias_id}
  Body:  { name }
  200:   { alias: Alias }

DELETE /v1/aliases/{alias_id}
  204    (soft delete)
```

### 5.4 Motions

```
GET    /v1/bases/{base_id}/motions
GET    /v1/aliases/{alias_id}/motions
  200:   { items: [Motion] }

POST   /v1/bases/{base_id}/motions
POST   /v1/aliases/{alias_id}/motions
  Body:  {
           motion_type: 'preset_wave' | 'preset_nod' | ... | 'custom',
           name: string,
           description: string | null  # custom only
         }
  202:   { task_id, motion_id }

GET    /v1/motions/{motion_id}
  200:   { motion: MotionDetail }

PATCH  /v1/motions/{motion_id}
  Body:  { name }  # custom only; preset 不可改名
  200:   { motion: Motion }

DELETE /v1/motions/{motion_id}
  204
```

### 5.5 Tasks

```
GET    /v1/tasks/{task_id}
  200:   { task: Task }

GET    /v1/tasks/{task_id}/stream
  200:   text/event-stream (SSE)
         data: {...}

POST   /v1/tasks/{task_id}/cancel
  200:   {
           task: Task,
           cancel_outcome: 'cancelled_immediately' | 'cancel_pending' | 'too_late_completed' | 'too_late_failed'
         }
  409:   AgentError if task already in terminal state BEFORE this call

   cancel_outcome 語義（完全解析 UX 需要的狀態）：
   - 'cancelled_immediately'
       → task 原本 queued，已從 queue 移除並設 status='cancelled'
       → UI: 立即顯示「已取消」
   - 'cancel_pending'
       → task 原本 running，已設 cancel_requested=true
       → Worker 會在下個 checkpoint 嘗試 abort
       → UI: 顯示「取消中...」+ 繼續訂閱 SSE 直到 final status
   - 'too_late_completed'
       → 呼叫 cancel 時 task 剛好 completed（race condition）
       → UI: 顯示「任務已完成（來不及取消）」+ 結果仍有效
   - 'too_late_failed'
       → 呼叫 cancel 時 task 剛好 failed
       → UI: 顯示「任務已失敗（來不及取消）」+ 顯示錯誤

   注意：cancel_pending 後的最終結果（cancelled / completed / failed）
         要透過 SSE 或 polling 繼續追蹤，不會透過 cancel endpoint 同步回傳。

GET    /v1/tasks
  Query: ?status=running&user_id=me
  200:   { items: [Task] }
         (看自己的任務列表)
```

### 5.6 Prompt Preview（F-04b 進階檢視）

```
POST   /v1/prompt/preview
  Body:  {
           mode: 'create_base' | 'create_alias' | 'create_motion',
           menu_selections?, freeform_note?, reference_image_ids?,
           mask?
         }
  200:   {
           platform_constraints: "...",
           reconciled_note_en: "...",
           menu_fragments: ["...", "..."],
           final_prompt: "..."
         }
```

**不執行生成，只回傳 prompt 組合結果**，給「進階檢視」按鈕用。

### 5.7 Usage / Quota（B6 軟性提醒）

```
GET    /v1/usage/me
  Query: ?period=current_month|all_time
  200:   {
           period: "2026-04",
           image_gen_count: 120,
           video_gen_count: 15,
           cost_units_total: 42.5,
           last_activity_at: "..."
         }
```

### 5.8 Signed URL 取用（本機 storage）

```
GET    /storage/{key:path}?token={jwt}&expires={ts}
  200:   binary (image / video)
  403:   AgentError
         - `STORAGE_URL_EXPIRED`（token 有效但過期）→ retryable=true
         - `AUTH_INVALID_TOKEN`（token 無效或簽章不對）→ retryable=false

   Frontend 行為對照：
   - STORAGE_URL_EXPIRED → 重新呼叫父資源 API（例如 GET /characters/{id}）取得新的 signed URL
   - AUTH_INVALID_TOKEN → 視為真的沒權限，不做 refresh
```

**不走 `/v1` 前綴**（是檔案 serving 而非 API）。

### 5.9 健康檢查 / 元資料

```
GET    /health
  200:   { status: 'ok', db: 'ok', storage: 'ok' }
         (DevOps 監控用，無須認證)

GET    /v1/meta
  200:   {
           models: { image: 'gpt-image-2', video: 'veo-3.1' },
           preset_motions: [...],
           platform_constraints_version: 'v1',
           api_version: 'v1',
           degraded_services: [
             {
               service: 'gpt-image-2',        // 'gpt-image-2' | 'veo-3.1' | 'reconciler'
               reason: 'CIRCUIT_OPEN',         // 'CIRCUIT_OPEN' | 'DEGRADED_FALLBACK' | 'RATE_LIMITED'
               retry_at: '2026-04-23T10:45:00Z' | null,  // 預計恢復時間（若可估）
               message: '模型暫時不可用，預計 5 分鐘後恢復'  // 給 UI 顯示
             }
             // 空陣列 [] 表示全部正常
           ]
         }

   說明：
   - Frontend 每 60s poll 此 endpoint
   - `degraded_services` 陣列對應 DegradedBanner 顯示
   - 來源：Redis 存 circuit breaker 狀態，此 endpoint 讀取聚合回傳
```

---

## 6. Resource Schema (DTO)

### 6.1 `Character`（列表用）

```json
{
  "id": "uuid",
  "name": "古風導覽員-小雅",
  "slug": "gu-feng-dao-lan-yuan-xiao-ya",
  "owner": { "id": "uuid", "name": "Alice" },
  "base_thumbnail_url": "https://.../storage/...?token=...",
  "alias_count": 3,
  "motion_count": 8,
  "created_at": "2026-04-20T08:15:00Z",
  "updated_at": "2026-04-23T10:30:00Z"
}
```

### 6.2 `CharacterDetail`

```json
{
  "id": "uuid",
  "name": "...",
  "slug": "...",
  "owner": { ... },
  "base": Base | null,                           // null when session still in progress
  "aliases": [Alias],
  "motions_summary": {
    "base": { "preset_generated": 3, "custom_count": 2 },
    "aliases": [{ "alias_id": "...", "preset_generated": 0, "custom_count": 1 }]
  },
  "creation_session": {                          // populated only when base === null
    "id": "uuid",
    "status": "in_progress" | "abandoned"        // 'completed' 不會出現（completed 必伴隨 base 寫入；此時欄位回 null）
  } | null,
  "created_at": "...",
  "updated_at": "...",
  "copied_from": { "character_id": "uuid", "name": "..." } | null
}
```

**`creation_session` 用途：** Frontend 在 `base` 為 null（character 尚未確立 Base）時，依 `creation_session.status` 決定行為：
- `in_progress` → 提供「繼續建立」按鈕導向 `/characters/new/session/{creation_session.id}`
- `abandoned` → 顯示「此 session 已被放棄」+ Back to Dashboard
- `null`（base 已確立）→ 不顯示 resume UI，正常 detail flow
- `null`（base 為 null 但 creation_session 也 null，異常狀態）→ fallback inline error

**Backend serializer 規則：** `base !== null` 時 `creation_session = null`（節省 payload + 清晰契約）；`base === null` 時 join `creation_sessions` 表並回 `{id, status}`。

### 6.3 `Base`

```json
{
  "id": "uuid",
  "character_id": "uuid",
  "image_url": "https://.../storage/...?token=...",
  "thumbnail_url": "...",
  "from_checkpoint_id": "uuid",
  "generation": { ... GenerationLog subset ... },
  "created_at": "..."
}
```

### 6.4 `Alias`

```json
{
  "id": "uuid",
  "character_id": "uuid",
  "name": "紅旗袍版",
  "input_mode": "image2image",
  "image_url": "...",
  "thumbnail_url": "...",
  "motion_count": 3,
  "created_at": "..."
}
```

### 6.5 `Motion`

```json
{
  "id": "uuid",
  "parent": { "type": "base" | "alias", "id": "uuid" },
  "motion_type": "preset_wave",
  "name": "招手歡迎",
  "description": null,
  "video_url": "...",
  "thumbnail_url": "...",     // 第一幀
  "duration_ms": 3500,
  "created_at": "..."
}
```

### 6.6 `Task`

```json
{
  "id": "uuid",
  "status": "running",
  "task_type": "create_alias",  // or 'create_checkpoint', 'create_motion', 'export_zip', 'copy_character'
  "entity_type": "alias",       // 對應建立中 entity type
  "entity_id": "uuid",          // 對應建立中 entity id
  "queue_position": null,       // 僅 status='queued' 時有值（int, 1-based）
  "progress": 0.3,              // 0..1，若可估算則有值
  "estimated_duration_ms": 30000,
  "cancel_requested": false,    // UX 判斷「取消中」狀態用
  "cancel_requested_at": null,
  "started_at": "...",
  "completed_at": null,
  "result": null,               // 完成時含 entity DTO（Alias / Motion / Checkpoint / ...）
  "error": null,                // 失敗時含 AgentError
  "created_at": "..."
}
```

**衍生狀態（Frontend 計算不需 backend 特別回）：**
- `cancel_requested=true && status='running'` → UI「取消中」
- `cancel_requested=true && status='cancelled'` → cancel 成功
- `cancel_requested=true && status in ('completed','failed')` → 來不及取消

### 6.7 `Checkpoint`

```json
{
  "id": "uuid",
  "creation_session_id": "uuid",
  "sequence": 3,
  "prompt_summary": "...",         // 壓縮版（UI 顯示），完整透過 prompt preview
  "output_image_url": "...",
  "thumbnail_url": "...",
  "selected_as_base": false,
  "created_at": "..."
}
```

### 6.8 `CreationSession`

```json
{
  "id": "uuid",
  "character_id": "uuid",
  "input_mode": "template",
  "status": "in_progress",
  "checkpoint_count": 5,
  "created_at": "...",
  "completed_at": null
}
```

---

## 7. Pagination

所有 list endpoint 統一用 **cursor-based pagination**：

```
GET /v1/characters?limit=20&cursor={last_id}
200: {
  items: [...],
  next_cursor: "uuid-or-null"
}
```

理由：cursor 比 offset 穩定（新增資料不會導致跳頁 / 重複），agent 也容易實作。

---

## 8. 需要 UX 協作決定的項目

這些我**先不在 API 層做決定**，等 UX step 2 給 flow 再補。

| 項目 | 為什麼等 UX |
|---|---|
| `Character` 列表的排序預設 | UI 層決定 |
| `motions_summary` 的具體欄位 | 看 UI 要顯示什麼 |
| Inpaint `mask` 格式（polygon vs bitmap vs bounding box）| UX 決定互動方式後才能定 |
| `Checkpoint.prompt_summary` 壓縮規則 | UX 決定要顯示什麼 |
| SSE event schema（要不要 partial preview 等）| UX 決定 loading 體驗 |
| Progress 要不要精細度（0.0-1.0）或只 coarse stages | UX 決定 progress bar 樣式 |

---

## 9. 給 UX Agent 的接口摘要

### UX 該知道的核心流程

**建立 Character（模式 A template）：**
```
1. POST /characters { name, input_mode: 'template' }
   → 拿到 character_id, creation_session_id
2. POST /creation-sessions/{id}/checkpoints { mode: 'fresh', menu_selections, freeform_note }
   → 拿到 task_id, checkpoint_id
3. GET /tasks/{task_id}/stream (SSE)
   → 等 completed
4. （使用者看 checkpoints）決定：重試 / remix / 從頭 / 確立為 base
5. POST /creation-sessions/{id}/select-base { checkpoint_id }
   → 完成
```

**建立 Character（模式 B reference）：**
```
1. POST /characters { ..., input_mode: 'reference' }
2. POST /creation-sessions/{id}/reference-images (multipart)
   → 拿到 reference_image_ids
3. POST /creation-sessions/{id}/checkpoints { mode: 'fresh', reference_image_ids, freeform_note }
4. 後續同模式 A
```

**新增 Alias：**
```
POST /characters/{id}/aliases { name, input_mode, freeform_note, reference_image_ids?, mask? }
→ Task → 完成
```

**生成 Motion：**
```
POST /bases/{id}/motions OR /aliases/{id}/motions
→ Task（Veo 3.1，可能 30-120s）→ 完成
```

**下載 ZIP：**
```
GET /characters/{id}/export → Task
Task 完成後：
GET /exports/{export_id}/download → redirect to signed URL
```

---

## 10. 下一步 Backend Agent 要做的（step 3 回補）

- Prompt Reconciler 的細節設計（哪個 LLM、prompt template、sanitization）
- Task queue 選型（Celery vs RQ vs arq vs 純 Postgres `LISTEN/NOTIFY`）
- AI model client 的 retry / timeout / circuit breaker
- Storage backend LocalFilesystemBackend 具體實作
- MCP server tool schema（若 Phase 1 要做雛形）

---

## 11. 關聯文件

- `../product/functional-scope.md` — 功能定義
- `../data/db-schema.md` — DB schema
- `../data/storage-layout.md` — 儲存層
- `CLAUDE.md` — Backend Agent 角色定位
- `api-shape.md` — 本文件（API shape draft）
