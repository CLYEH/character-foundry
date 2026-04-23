# Character Foundry — Data Lifecycle & Backup

> **Status:** Draft v0.1 · 2026-04-23
> **Owner:** Data Agent

---

## 1. Entity 生命週期總覽

| Entity | 建立 | 可更新欄位 | 刪除策略 | 檔案清理 |
|---|---|---|---|---|
| Team | Bootstrap 時建 default | `name` | 不支援（Phase 1）| N/A |
| User | Admin 建立 | `name`, `last_login_at`, `password_hash` | Hard (RESTRICT) | N/A |
| Character | CreationSession 完成時 | `name`, `updated_at` | **Soft** → 30 天後 hard | 跟隨 hard delete |
| CreationSession | 進入 creation flow 時 | `status`, `completed_at`, `character_id` | Cascade (character hard delete) | Checkpoint 檔案刪 |
| Checkpoint | 每次生成候選圖 | （無，immutable）| Cascade (session delete) | 圖片刪 |
| Base | Creation 完成確立時 | （無，immutable）| Cascade (character hard delete) | 圖片刪 |
| Alias | 使用者新增 alias | `name` | **Soft** → 30 天後 hard | 圖片刪 |
| Motion | 使用者按生成 | `name` (custom only) | **Soft** → 30 天後 hard | 影片刪 |
| GenerationLog | AI 生成開始時 | `status`, `completed_at`, `error_message`, `duration_ms` | 按 partition drop（12 個月）| input_image_keys 檔案保留 |
| Task | API 接到 async 請求時 | `status`, `progress`, `started_at`, `completed_at`, `result`, `error`, `cancel_requested` | Hard delete 24h after terminal | N/A（input_payload 內的 key 由 entity 本身管）|

---

## 2. Creation Session 生命週期

這是**最複雜的 flow**，需要嚴謹的狀態轉換。

### 2.1 狀態機

```
    [start]
       │
       ▼
  ┌─────────────┐
  │ in_progress │──── user picks checkpoint ──▶ [completed]
  │             │                                    │
  │             │                                    ▼
  │             │                          Character.base_id set
  │             │                          CreationSession.completed_at = NOW()
  │             │                          Character.creation_session_id set
  │             │
  │             │──── user closes / timeout ──▶ [abandoned]
  │             │                                    │
  │             │                                    ▼
  │             │                          Scheduled job: 7 天後
  │             │                          刪 session + checkpoints + 檔案
  └─────────────┘
```

### 2.2 狀態轉換規則

**`in_progress` → `completed`：**
- 使用者從該 session 的 checkpoints 中挑一張
- 系統產生 Base entity（引用該 checkpoint）
- 系統產生 Character entity（引用 Base + Session）
- Session.status = 'completed'
- 該 session 的所有 checkpoints（含未選中的）**保留**

**`in_progress` → `abandoned`：**
- 使用者離開頁面且 30 分鐘未回來 → **scheduled job** 標記 abandoned
- 使用者主動「取消」→ 立即標記 abandoned
- Abandoned session 的 checkpoints 保留 **7 天**，過期後 cascade 刪除

### 2.3 Orphaned Session 清理

每日 scheduled job：

```sql
-- Mark abandoned
UPDATE creation_sessions
SET status = 'abandoned'
WHERE status = 'in_progress'
  AND created_at < NOW() - INTERVAL '24 hours';

-- Delete abandoned > 7 days
DELETE FROM creation_sessions
WHERE status = 'abandoned'
  AND created_at < NOW() - INTERVAL '7 days';
-- Cascade 會刪除 checkpoints，Backend 觸發檔案清理
```

---

## 2.5 Task 生命週期

### 2.5.1 狀態機

```
  [queued] ────worker pick up────▶ [running]
     │                                │
     │                                ├── success ──▶ [completed]
     │                                ├── error ────▶ [failed]
     │                                └── cancel ───▶ [cancelled]
     │
     └── user cancel before pick ──▶ [cancelled]
```

### 2.5.2 狀態轉換規則

| 轉換 | 觸發 | 副作用 |
|---|---|---|
| `queued → running` | Worker pick up | `started_at = NOW()` |
| `running → completed` | Worker 寫完 entity | `completed_at = NOW()`, `result = { ... }` |
| `running → failed` | Worker throw exception | `completed_at = NOW()`, `error = AgentError` |
| `queued → cancelled` | User cancel（立即）| `completed_at = NOW()`, `cancel_requested = TRUE` |
| `running → cancelled` | User cancel + worker abort 成功 | 同上 |
| `running → completed/failed` | User cancel 但 worker 來不及停 | 忽略 cancel_requested，照原終止 |

### 2.5.3 Cancel 語義

詳見 `../backend/task-queue.md` §7。資料層只需：
- `cancel_requested` BOOLEAN 標記意圖
- `cancel_requested_at` 記錄時間
- 狀態最終由 worker 決定（可能仍 completed 或 failed）

### 2.5.4 Terminal state 保留 24 小時

每小時 scheduled job：

```sql
DELETE FROM tasks
WHERE status IN ('completed', 'failed', 'cancelled')
  AND completed_at < NOW() - INTERVAL '24 hours';
```

理由：
- UI 需要短期內查 task 狀態（讓 Toast 點擊「詳細」能 work）
- Agent 透過 webhook 拿到結果後不會再 poll
- `GenerationLog` 保留長期審計紀錄，`tasks` 只是短期執行狀態

### 2.5.5 與 `generation_logs` 的關係

- 每個 AI 生成類 task（`create_checkpoint` / `create_alias` / `create_motion`）→ 會**寫一筆 `generation_logs`**
- 兩者不是 1:1：一個 task 失敗重試可能產生多筆 generation_logs
- Task 刪除（24h 後）不影響 generation_logs
- 反之，generation_logs partition drop（12 個月）時 tasks 早就沒了

### 2.5.6 Orphan tasks

如果 worker crash 且 task 卡在 `running` 超過 1 小時：

```sql
-- 每 15 分鐘 scheduled job 掃 stuck tasks
UPDATE tasks
SET status = 'failed',
    completed_at = NOW(),
    error = '{"code":"INTERNAL_WORKER_TIMEOUT", ...}'::JSONB
WHERE status = 'running'
  AND started_at < NOW() - INTERVAL '1 hour';
```

Worker 啟動時也可 re-enqueue orphan queued tasks（arq 本身會處理 Redis-level recovery）。

---

## 3. Soft Delete 策略

### 3.1 哪些走 Soft Delete

- `characters.deleted_at`
- `aliases.deleted_at`
- `motions.deleted_at`

### 3.2 ORM 層全域 filter

SQLAlchemy 建議方式：

```python
# Base query 預設過濾 deleted
class SoftDeleteMixin:
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    @classmethod
    def query_active(cls):
        return cls.query.filter(cls.deleted_at.is_(None))

    def soft_delete(self):
        self.deleted_at = func.now()
```

**注意：** 如果走 global filter，admin tool 要能繞過（`include_deleted=True`）。

### 3.3 刪除 Character 的連鎖行為

```
user soft-deletes Character:
  Character.deleted_at = NOW()
  └── 隱含：所有 active aliases, motions 對外不可見
      （但 DB 記錄仍在，可 restore）

30 天後 scheduled job:
  Character hard delete
  └── Cascade: CreationSession (character-linked), Checkpoints, Base
  └── Aliases 跟著 Character hard delete 也要 hard delete
  └── Motions 跟著 aliases / base hard delete 也要 hard delete
  └── 所有相關檔案從 storage 清除
  └── GenerationLog.character_id 設為 NULL（SET NULL on FK）
```

### 3.4 Restore 流程（admin tool）

```sql
-- Restore character
UPDATE characters SET deleted_at = NULL WHERE id = :id;

-- Restore all aliases / motions（如果 admin 確認要一起還原）
UPDATE aliases SET deleted_at = NULL WHERE character_id = :id AND deleted_at > :character_deleted_at;
UPDATE motions SET deleted_at = NULL WHERE base_id IN (...) AND deleted_at > :character_deleted_at;
```

---

## 4. Hard Delete / Partition Drop

### 4.1 Character 30 天後 hard delete

每日 scheduled job：

```python
def hard_delete_expired():
    expired = Character.query.filter(
        Character.deleted_at < datetime.now() - timedelta(days=30)
    ).all()

    for char in expired:
        # 1. 刪檔案
        storage.delete_prefix(f"characters/{char.id}/")

        # 2. DB hard delete（cascade 處理關聯）
        db.session.delete(char)

    db.session.commit()
```

### 4.2 GenerationLog partition rotation

每月 1 日 scheduled job：

```sql
-- 1. Create next month's partition
CREATE TABLE generation_logs_YYYY_MM PARTITION OF generation_logs
    FOR VALUES FROM ('YYYY-MM-01') TO ('YYYY-NEXT-MM-01');

-- 2. Detach oldest partition (13 months ago)
ALTER TABLE generation_logs DETACH PARTITION generation_logs_OLD_YYYY_MM;

-- 3. Dump to file
pg_dump -t generation_logs_OLD_YYYY_MM > /backup/archives/gen_logs_OLD_YYYY_MM.sql

-- 4. Drop
DROP TABLE generation_logs_OLD_YYYY_MM;
```

封存檔案保留 **5 年**（符合一般審計需求）。

---

## 5. 檔案清理與 DB 一致性

### 5.1 問題：DB 刪了，檔案沒刪（orphan files）

發生情境：
- `DELETE` 成功，`storage.delete()` 失敗
- Backend crash 在 commit 跟 storage delete 之間

### 5.2 解決策略：**DB 優先 + Reconciliation job**

1. 刪除順序：**先刪 storage，再 commit DB**（失敗重試）
2. 每週 reconciliation job：掃 storage，找 DB 沒對應的 key，刪除

```python
# Weekly reconciliation
def find_orphan_files():
    all_storage_keys = storage.list_prefix("characters/")

    active_character_ids = set(
        str(c.id) for c in Character.query.all()  # 含 soft-deleted
    )

    for key in all_storage_keys:
        character_id = extract_character_id_from_key(key)
        if character_id not in active_character_ids:
            storage.delete(key)  # orphan
```

### 5.3 反向問題：DB 有紀錄、檔案沒了

- API 讀取時 `NotFoundError` → backend 回 `GONE` 錯誤
- 使用者看到「此素材已遺失，請聯繫管理員」
- Admin tool 可批次偵測 DB 有紀錄但 storage 沒檔案的情況

---

## 6. Backup 策略（D4）

### 6.1 Phase 1 備份內容

| 項目 | 頻率 | 工具 | 保留 |
|---|---|---|---|
| PostgreSQL | 每日 | `pg_dump -Fc` | 30 天 |
| STORAGE_ROOT | 每日 | `tar --listed-incremental` (增量) | 7 天完整 + 30 天增量 |
| Secret / config | 手動 + git | - | git history |

### 6.2 備份腳本示意

```bash
#!/bin/bash
BACKUP_DIR=/backup/$(date +%Y-%m-%d)
mkdir -p $BACKUP_DIR

# DB
pg_dump -Fc -f $BACKUP_DIR/character_foundry.dump character_foundry

# Files (incremental)
tar --listed-incremental=/backup/snapshot.tar \
    -czf $BACKUP_DIR/storage.tar.gz \
    /var/character-foundry/storage

# Cleanup > 30 days
find /backup -maxdepth 1 -type d -mtime +30 -exec rm -rf {} \;
```

DevOps Agent 負責 cron / systemd timer 設定。

### 6.3 Backup 儲存位置

**Phase 1：** 同機器不同 volume（至少不同 disk partition）。
**Phase 2：** 異地備份（另一台機器 / 雲端 S3 glacier）。

### 6.4 Restore 演練

**建議每季度做一次 restore 演練：**
1. 準備乾淨測試機
2. Restore DB + storage
3. 執行 smoke test（登入、開 Character、下載 ZIP）
4. 記錄恢復時間（RTO）

---

## 7. 資料遷移（未來使用）

### 7.1 Schema migration

Alembic 管理。
- 每個 migration 必須可回滾（`downgrade()` 實作）
- 含資料遷移的 migration 要 dry-run 過 staging DB

### 7.2 Storage backend 切換（local → S3）

步驟：
1. 新增 `S3Backend` 實作
2. Config flag：`STORAGE_BACKEND=s3`
3. Migration script：迭代所有 storage key，從 local 讀、寫入 S3
4. 驗證所有 key 可透過 new backend 讀取
5. 切換 config
6. 等 1 週無異常後刪除 local files

因為 DB 存的是 `key` 不是 URL，**這個切換不影響 DB**。

---

## 8. Vector Embedding 生命週期（pgvector）

### 8.1 何時生成

- Base / Alias 圖片產出時，順便算 CLIP embedding → 存進 `image_embedding` 欄位
- Checkpoint 圖片產出時同樣
- 若 API 呼叫失敗 → embedding 欄位 null，之後可 backfill

### 8.2 Re-embedding

若更換 CLIP 模型：
1. 新 schema：`image_embedding_v2 vector(新維度)`
2. Scheduled job 逐步 backfill
3. 切換 API 查詢到 v2
4. Drop v1

---

## 9. 關聯文件

- `db-schema.md` — DB schema
- `storage-layout.md` — 檔案儲存
- `../devops/CLAUDE.md` — DevOps 負責 scheduled job / cron / backup 執行
- `../backend/CLAUDE.md` — Backend 實作 soft delete / reconciliation / storage delete
