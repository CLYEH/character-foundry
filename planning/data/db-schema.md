# Character Foundry — DB Schema

> **Status:** Draft v0.1 · 2026-04-23
> **Owner:** Data Agent
> **Based on:** `../product/data-model.md`
> **DBMS:** PostgreSQL 15+ with `pgvector` extension

---

## 1. 技術棧決定

| 項目 | 選擇 | 原因 |
|---|---|---|
| DBMS | **PostgreSQL 15+** | JSONB 原生、完整 FK/transaction、`pgvector` 可原地加 semantic search |
| ORM | **SQLAlchemy 2.x** | 與 FastAPI 配套、支援 async |
| Migration | **Alembic** | SQLAlchemy 標準 migration tool、版本化、可回滾 |
| Vector | **pgvector** | 原生 PG extension，無需額外 DB |
| Connection | **asyncpg** via SQLAlchemy | 支援 async I/O，i2v 長任務不阻塞 |

### Extensions 需求

```sql
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";    -- UUID generation
CREATE EXTENSION IF NOT EXISTS "pgcrypto";     -- gen_random_uuid() alternative
CREATE EXTENSION IF NOT EXISTS vector;         -- pgvector (semantic search)
CREATE EXTENSION IF NOT EXISTS pg_trgm;        -- 模糊搜尋 (for name search)
```

---

## 2. 刪除策略（Hybrid — D3 決定）

| Entity | 策略 | 說明 |
|---|---|---|
| `teams` | Hard (Phase 1 不預期刪) | RESTRICT on FK |
| `users` | Hard (Phase 1 不做 UI) | RESTRICT on FK，避免意外刪除 |
| `characters` | **Soft** (`deleted_at`) | 使用者可能誤刪，留後悔藥 |
| `creation_sessions` | Cascade on character hard delete | Session 只跟某 Character 綁定 |
| `checkpoints` | Cascade on session delete | Immutable，但隨 session 走 |
| `bases` | Cascade on character hard delete | 1:1 跟 character |
| `aliases` | **Soft** (`deleted_at`) | 同上 |
| `motions` | **Soft** (`deleted_at`) | 同上 |
| `generation_logs` | Hard via partition drop | 審計紀錄 12 個月後封存 |
| `tasks` | Hard via scheduled cleanup | completed/failed/cancelled 24h 後刪除 |

**Soft delete 規則：**
- 刪除時設 `deleted_at = NOW()`
- 所有 UNIQUE INDEX 帶 `WHERE deleted_at IS NULL` partial index
- 所有查詢預設帶 `WHERE deleted_at IS NULL`（ORM 層的 global filter）
- Admin 工具可 restore（`UPDATE ... SET deleted_at = NULL`）
- 30 天後 hard delete（scheduled job）

---

## 3. Schema DDL

### 3.1 `teams`

```sql
CREATE TABLE teams (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Phase 1 bootstrap：只有一筆
INSERT INTO teams (name) VALUES ('default');
```

### 3.2 `users`

```sql
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id UUID NOT NULL REFERENCES teams(id) ON DELETE RESTRICT,
    name VARCHAR(100) NOT NULL,
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login_at TIMESTAMPTZ
);

CREATE INDEX idx_users_team ON users(team_id);
```

### 3.3 `characters`

```sql
CREATE TABLE characters (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id UUID NOT NULL REFERENCES teams(id) ON DELETE RESTRICT,
    owner_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    name VARCHAR(50) NOT NULL,
    slug VARCHAR(60) NOT NULL,
    base_id UUID,  -- FK added after bases table exists (see §3.6)
    creation_session_id UUID,  -- FK added after creation_sessions table
    copied_from_character_id UUID REFERENCES characters(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ,

    CONSTRAINT chk_characters_name_length
        CHECK (char_length(name) BETWEEN 1 AND 50),
    -- PostgreSQL ARE does not support \p{...}; literal CJK range U+4E00–U+9FFF.
    CONSTRAINT chk_characters_name_chars
        CHECK (name ~ '^[一-鿿a-zA-Z0-9_-]+$')  -- 中文、英數、底線、連字號
);

-- Soft-delete-aware UNIQUE：per-owner 唯一（不同 owner 可同名）
CREATE UNIQUE INDEX uq_characters_owner_name
    ON characters(owner_id, name)
    WHERE deleted_at IS NULL;

CREATE UNIQUE INDEX uq_characters_owner_slug
    ON characters(owner_id, slug)
    WHERE deleted_at IS NULL;

CREATE INDEX idx_characters_team ON characters(team_id)
    WHERE deleted_at IS NULL;

CREATE INDEX idx_characters_owner ON characters(owner_id)
    WHERE deleted_at IS NULL;

-- 名稱模糊搜尋（pg_trgm）
CREATE INDEX idx_characters_name_trgm ON characters USING gin (name gin_trgm_ops)
    WHERE deleted_at IS NULL;

-- Auto-update updated_at trigger
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER trg_characters_updated_at
    BEFORE UPDATE ON characters
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
```

### 3.4 `creation_sessions`

```sql
CREATE TABLE creation_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    character_id UUID REFERENCES characters(id) ON DELETE CASCADE,  -- nullable while in_progress
    initiator_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    input_mode VARCHAR(20) NOT NULL
        CHECK (input_mode IN ('template', 'reference')),
    status VARCHAR(20) NOT NULL DEFAULT 'in_progress'
        CHECK (status IN ('in_progress', 'completed', 'abandoned')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

-- 補上 characters.creation_session_id FK
ALTER TABLE characters
    ADD CONSTRAINT fk_characters_creation_session
    FOREIGN KEY (creation_session_id) REFERENCES creation_sessions(id) ON DELETE SET NULL;

CREATE INDEX idx_sessions_initiator ON creation_sessions(initiator_id);
CREATE INDEX idx_sessions_character ON creation_sessions(character_id)
    WHERE character_id IS NOT NULL;
CREATE INDEX idx_sessions_in_progress ON creation_sessions(status)
    WHERE status = 'in_progress';
```

### 3.5 `checkpoints`

```sql
CREATE TABLE checkpoints (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    creation_session_id UUID NOT NULL REFERENCES creation_sessions(id) ON DELETE CASCADE,
    sequence INT NOT NULL,
    prompt TEXT NOT NULL,  -- 完整英文 prompt（含 platform constraints）
    user_menu_selections JSONB,
    user_freeform_note TEXT,  -- 原文（中文）
    reference_image_keys TEXT[],
    seed VARCHAR(100),
    output_image_key TEXT NOT NULL,
    output_image_embedding vector(768),  -- CLIP ViT-L/14
    generation_log_id UUID,  -- FK added later
    selected_as_base BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_session_sequence UNIQUE (creation_session_id, sequence)
);

-- 不另建 (creation_session_id, sequence) 索引：UNIQUE 約束 `uq_session_sequence`
-- 已自動建立對應的 btree 索引，對於以 `creation_session_id` 為 prefix 的查詢也能命中。

-- Vector similarity search index（IVFFlat，適合 <1M 筆）
CREATE INDEX idx_checkpoints_embedding
    ON checkpoints USING ivfflat (output_image_embedding vector_cosine_ops)
    WITH (lists = 100);
```

### 3.6 `bases`

```sql
CREATE TABLE bases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    character_id UUID NOT NULL UNIQUE REFERENCES characters(id) ON DELETE CASCADE,
    from_checkpoint_id UUID NOT NULL REFERENCES checkpoints(id) ON DELETE RESTRICT,
    image_key TEXT NOT NULL,
    image_embedding vector(768),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 補上 characters.base_id FK（解 circular dependency）
ALTER TABLE characters
    ADD CONSTRAINT fk_characters_base
    FOREIGN KEY (base_id) REFERENCES bases(id) ON DELETE SET NULL;

-- 不另建 character_id 索引：欄位層 UNIQUE 已隱式建出可用的 btree 索引。
CREATE INDEX idx_bases_embedding
    ON bases USING ivfflat (image_embedding vector_cosine_ops)
    WITH (lists = 100);
```

### 3.7 `aliases`

```sql
CREATE TABLE aliases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    character_id UUID NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    name VARCHAR(50) NOT NULL,
    prompt TEXT NOT NULL,
    user_freeform_note TEXT,
    input_mode VARCHAR(30) NOT NULL
        CHECK (input_mode IN ('image2image', 'inpaint', 'text2image', 'mixed')),
    mask_data JSONB,  -- 若用 inpaint，存 bounding box / mask metadata
    image_key TEXT NOT NULL,
    image_embedding vector(768),
    generation_log_id UUID,  -- FK added later
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ,

    CONSTRAINT chk_aliases_name_length
        CHECK (char_length(name) BETWEEN 1 AND 50),
    -- PostgreSQL ARE does not support \p{...}; literal CJK range U+4E00–U+9FFF.
    CONSTRAINT chk_aliases_name_chars
        CHECK (name ~ '^[一-鿿a-zA-Z0-9_-]+$')
);

CREATE UNIQUE INDEX uq_aliases_character_name
    ON aliases(character_id, name)
    WHERE deleted_at IS NULL;

CREATE INDEX idx_aliases_character ON aliases(character_id)
    WHERE deleted_at IS NULL;

CREATE INDEX idx_aliases_embedding
    ON aliases USING ivfflat (image_embedding vector_cosine_ops)
    WITH (lists = 100);
```

### 3.8 `motions`

使用 **Option B**（`base_id` / `alias_id` 二選一），保持真實 FK 約束。

```sql
CREATE TABLE motions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    base_id UUID REFERENCES bases(id) ON DELETE CASCADE,
    alias_id UUID REFERENCES aliases(id) ON DELETE CASCADE,
    motion_type VARCHAR(30) NOT NULL
        CHECK (motion_type IN (
            'preset_wave', 'preset_nod', 'preset_gesture',
            'preset_happy', 'preset_idle', 'custom'
        )),
    name VARCHAR(50) NOT NULL,
    description TEXT,  -- Custom motion 才會有值
    video_key TEXT NOT NULL,
    duration_ms INT,
    generation_log_id UUID,  -- FK added later
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ,

    -- 恰好一個 parent
    CONSTRAINT chk_motions_exactly_one_parent
        CHECK (
            (base_id IS NOT NULL AND alias_id IS NULL) OR
            (base_id IS NULL AND alias_id IS NOT NULL)
        ),

    CONSTRAINT chk_motions_name_length
        CHECK (char_length(name) BETWEEN 1 AND 50),
    -- PostgreSQL ARE does not support \p{...}; literal CJK range U+4E00–U+9FFF.
    CONSTRAINT chk_motions_name_chars
        CHECK (name ~ '^[一-鿿a-zA-Z0-9_-]+$'),

    -- Custom motion 必須有 description
    CONSTRAINT chk_motions_custom_has_description
        CHECK (motion_type != 'custom' OR description IS NOT NULL)
);

-- 同 parent 下名稱唯一（Base 的 motion 跟 Alias 的 motion 分別 UNIQUE）
CREATE UNIQUE INDEX uq_motions_base_name
    ON motions(base_id, name)
    WHERE base_id IS NOT NULL AND deleted_at IS NULL;

CREATE UNIQUE INDEX uq_motions_alias_name
    ON motions(alias_id, name)
    WHERE alias_id IS NOT NULL AND deleted_at IS NULL;

CREATE INDEX idx_motions_base ON motions(base_id)
    WHERE base_id IS NOT NULL AND deleted_at IS NULL;
CREATE INDEX idx_motions_alias ON motions(alias_id)
    WHERE alias_id IS NOT NULL AND deleted_at IS NULL;
```

### 3.9 `generation_logs` (partitioned monthly)

```sql
CREATE TABLE generation_logs (
    id UUID NOT NULL DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    character_id UUID REFERENCES characters(id) ON DELETE SET NULL,
    entity_type VARCHAR(20) NOT NULL
        CHECK (entity_type IN ('checkpoint', 'alias', 'motion')),
    entity_id UUID,
    model_name VARCHAR(50) NOT NULL,     -- e.g. 'gpt-image-2', 'veo-3.1'
    model_version VARCHAR(30),
    final_prompt TEXT NOT NULL,
    input_image_keys TEXT[],
    parameters JSONB,
    cost_units DECIMAL(10, 4) NOT NULL DEFAULT 0,
    status VARCHAR(20) NOT NULL
        CHECK (status IN ('success', 'failed', 'timeout', 'running')),
    error_message TEXT,
    duration_ms INT,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,

    PRIMARY KEY (id, started_at)  -- partition key 必須包含在 PK
) PARTITION BY RANGE (started_at);

-- 補上其他表的 generation_log_id FK
-- 注意：partitioned table 不支援 FK referencing，所以要 application-level 保證
-- 或者 generation_log_id 不加 FK，僅當作軟關聯

-- 每月一個 partition（bootstrap script 建當月 + 未來 6 個月）
CREATE TABLE generation_logs_2026_04 PARTITION OF generation_logs
    FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
CREATE TABLE generation_logs_2026_05 PARTITION OF generation_logs
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
-- ...

-- 每個 partition 上的 index
CREATE INDEX idx_gen_logs_2026_04_user_time
    ON generation_logs_2026_04 (user_id, started_at DESC);
CREATE INDEX idx_gen_logs_2026_04_character
    ON generation_logs_2026_04 (character_id)
    WHERE character_id IS NOT NULL;
CREATE INDEX idx_gen_logs_2026_04_status
    ON generation_logs_2026_04 (status)
    WHERE status IN ('running', 'failed');
```

**Partition 管理：**
- 每月 1 日 scheduled job 建下個月 partition
- 13 個月以上的 partition 每月 1 日 `DETACH` + dump 到檔案後 `DROP`（見 `lifecycle.md`）

**Partitioned table 不支援被 FK reference**：Checkpoint/Alias/Motion 的 `generation_log_id` 欄位**不建 FK**，application 層保證一致性。

### 3.10 `user_usage_summary` (materialized view)

```sql
CREATE MATERIALIZED VIEW user_usage_summary AS
SELECT
    user_id,
    to_char(started_at, 'YYYY-MM') AS period,
    COUNT(*) FILTER (WHERE model_name = 'gpt-image-2' AND status = 'success') AS image_gen_count,
    COUNT(*) FILTER (WHERE model_name = 'veo-3.1' AND status = 'success') AS video_gen_count,
    COALESCE(SUM(cost_units) FILTER (WHERE status = 'success'), 0) AS cost_units_total,
    MAX(started_at) AS last_activity_at
FROM generation_logs
GROUP BY user_id, to_char(started_at, 'YYYY-MM');

CREATE UNIQUE INDEX idx_usage_user_period ON user_usage_summary(user_id, period);

-- Refresh 策略（Phase 1 用 scheduled job，每小時一次）：
-- REFRESH MATERIALIZED VIEW CONCURRENTLY user_usage_summary;
```

**Phase 1 量小可改用即時 query**，不建 view：

```sql
-- 等價查詢（直接在 API 層呼叫）
SELECT
    COUNT(*) FILTER (WHERE model_name = 'gpt-image-2') AS image_gen_count,
    COUNT(*) FILTER (WHERE model_name = 'veo-3.1') AS video_gen_count,
    COALESCE(SUM(cost_units), 0) AS cost_units_total
FROM generation_logs
WHERE user_id = :user_id
  AND started_at >= date_trunc('month', NOW())
  AND status = 'success';
```

**建議 Phase 1 先直接 query**，等 `generation_logs` 筆數超過 100k 再切 materialized view。

### 3.11 `tasks`

非同步任務的事實來源。Backend 的 arq queue（Redis）負責排程與執行，但所有 task 狀態 / 結果 / 錯誤由這張表為主（API 查 task 狀態直接讀這張表）。

```sql
CREATE TABLE tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,

    task_type VARCHAR(50) NOT NULL
        CHECK (task_type IN (
            'create_checkpoint',
            'create_alias',
            'create_motion',
            'export_zip',
            'copy_character'
        )),

    status VARCHAR(20) NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled')),

    -- 關聯到最終產物（若有）— 非 strict FK，應用層保證
    entity_type VARCHAR(30)
        CHECK (entity_type IS NULL OR entity_type IN (
            'checkpoint', 'alias', 'motion', 'character', 'export'
        )),
    entity_id UUID,

    -- 進度 0.0-1.0（null = 不支援 progress 的任務）
    progress REAL
        CHECK (progress IS NULL OR (progress >= 0 AND progress <= 1)),

    -- 估計完成時間（ms），由 task-queue.md §4 的邏輯計算
    estimated_duration_ms INT,

    -- 輸入 / 參數（存成 JSONB 方便 retry 或 debug）
    input_payload JSONB NOT NULL,

    -- 結果（成功時 = entity 摘要 DTO）
    result JSONB,

    -- 錯誤（失敗時 = AgentError JSON per api-shape.md §4）
    error JSONB,

    -- 時序
    queued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,

    -- 取消機制
    cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
    cancel_requested_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- 狀態一致性：completed/failed/cancelled 必須有 completed_at
    CONSTRAINT chk_tasks_terminal_completed_at
        CHECK (
            (status IN ('queued', 'running') AND completed_at IS NULL)
            OR
            (status IN ('completed', 'failed', 'cancelled') AND completed_at IS NOT NULL)
        ),

    -- Result / error 互斥（success 有 result、failed 有 error、cancelled 可能都 null）
    CONSTRAINT chk_tasks_result_error_mutex
        CHECK (NOT (result IS NOT NULL AND error IS NOT NULL))
);

-- 使用者的任務列表（最近活動查詢）
CREATE INDEX idx_tasks_user_status_created
    ON tasks(user_id, status, created_at DESC);

-- Worker pick up + queue depth 查詢
CREATE INDEX idx_tasks_active
    ON tasks(queued_at)
    WHERE status IN ('queued', 'running');

-- 找出某 entity 是哪個 task 建出來的（debug / audit）
CREATE INDEX idx_tasks_entity
    ON tasks(entity_type, entity_id)
    WHERE entity_id IS NOT NULL;

-- Cancel in progress（scheduled job 可能要掃）
CREATE INDEX idx_tasks_cancel_pending
    ON tasks(id)
    WHERE cancel_requested = TRUE AND status = 'running';
```

**設計要點：**

- **`entity_type` 非 strict FK**：因為 `entity_id` 可能指向不同表（`checkpoints` / `aliases` / `motions` / `characters`），polymorphic 關係，應用層保證正確性
- **`status` + `completed_at` 一致性約束**：CHECK 確保終止狀態必有時間戳，避免「completed 但 completed_at 為 null」這種 bug
- **`result` / `error` 互斥**：一個 task 不可能同時有兩者
- **`input_payload` 存完整輸入**：之後 retry、debug、重建 prompt 都用這欄，不靠 request 重送

**跟其他表的關係：**

- `user_id` → `users`（RESTRICT，使用者不能隨便刪除）
- `entity_id` → 軟關聯到 checkpoints / aliases / motions / characters / exports（應用層保證）
- **不被其他表 FK**（Task 是執行紀錄，被刪除不影響其他資料）

---

## 4. Slug 演算法

| 步驟 | 處理 |
|---|---|
| 1 | 中文 → 拼音（用 `pypinyin` library，Tone-free） |
| 2 | 英數字 → 小寫 |
| 3 | 空白 / 底線 / 連字號統一為 `-` |
| 4 | 移除非 `[a-z0-9\-]` 字元 |
| 5 | 收斂連續 `-`，修剪頭尾 `-` |
| 6 | 最長 60 字元 |
| 7 | 查詢 `(owner_id, slug)` 衝突 → 加後綴 `-2`, `-3`, ...（最多 100 次） |
| 8 | 仍衝突 → 加 UUID prefix 4 碼 |

範例：
- `"古風導覽員-小雅"` → `gu-feng-dao-lan-yuan-xiao-ya`
- 衝突 → `gu-feng-dao-lan-yuan-xiao-ya-2`
- 再衝突 → `gu-feng-dao-lan-yuan-xiao-ya-3`

Implementation：Backend Agent 實作，Data Agent 負責測試 case。

---

## 5. Index 策略總覽

| Table | Index | 用途 |
|---|---|---|
| `users` | `email` UNIQUE | 登入 |
| `characters` | `(owner_id, name)` UNIQUE partial | 命名唯一性 |
| `characters` | `(owner_id, slug)` UNIQUE partial | URL 唯一性 |
| `characters` | `name` GIN + trgm | 模糊搜尋 |
| `characters` | `team_id` partial | 團隊列表 |
| `checkpoints` | `(creation_session_id, sequence)` UNIQUE | 時序（此 UNIQUE 自帶 btree 索引，不額外建 idx） |
| `checkpoints` | `embedding` IVFFlat | 相似搜尋 |
| `bases` | `character_id` UNIQUE | 1:1 保證（此 UNIQUE 自帶 btree 索引，不額外建 idx） |
| `aliases` | `(character_id, name)` UNIQUE partial | 命名唯一性 |
| `motions` | `(base_id, name)` UNIQUE partial | Base motion 命名 |
| `motions` | `(alias_id, name)` UNIQUE partial | Alias motion 命名 |
| `generation_logs` | `(user_id, started_at DESC)` | 使用量查詢 |
| `generation_logs` | `(character_id)` partial | 按角色查 log |
| `tasks` | `(user_id, status, created_at DESC)` | 使用者任務列表 |
| `tasks` | `(queued_at)` partial `queued/running` | Worker 輪詢 + queue depth |
| `tasks` | `(entity_type, entity_id)` partial | Entity 的建立歷史 |
| `tasks` | `(id)` partial `cancel_requested + running` | 掃待取消的執行中任務 |

---

## 6. PM §7 未決問題解答

| 問題 | 解答 |
|---|---|
| 唯一性 scope 索引 | `(owner_id, name)` UNIQUE partial（Phase 1 單 team 不含 team_id），未來 multi-team 需補 `team_id` |
| Slug 演算法 | 見 §4 |
| GenerationLog partition | **是**，按月 partition。保留 12 個月後封存 |
| 檔案路徑含使用者維度 | **否**，走 `/characters/{id}/...`。ownership 在 DB 層保證，不靠路徑 |

---

## 7. Migration 順序

Alembic migration 順序（避免 FK 循環）：

1. `20260423_001_create_extensions.py` — Extensions
2. `20260423_002_create_teams.py` — teams + default team insert
3. `20260423_003_create_users.py` — users
4. `20260423_004_create_characters_skeleton.py` — characters（無 FK to bases/sessions）
5. `20260423_005_create_creation_sessions.py` — creation_sessions + FK back to characters
6. `20260423_006_create_checkpoints.py` — checkpoints
7. `20260423_007_create_bases.py` — bases + FK back to characters
8. `20260423_008_create_aliases.py` — aliases
9. `20260423_009_create_motions.py` — motions
10. `20260423_010_create_generation_logs.py` — partitioned generation_logs + initial partitions
11. `20260423_011_create_tasks.py` — tasks table（async task 追蹤，Backend arq 的事實來源）
12. `20260423_012_create_triggers.py` — updated_at triggers
13. `20260423_013_create_usage_summary.py` — materialized view（若採用）

---

## 8. 關聯文件

- `../product/data-model.md` — 邏輯資料模型
- `storage-layout.md` — 檔案儲存設計
- `lifecycle.md` — 生命週期與備份
- `../backend/CLAUDE.md` — Backend Agent 會基於此設計 ORM models
