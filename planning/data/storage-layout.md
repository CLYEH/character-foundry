# Character Foundry — Storage Layout

> **Status:** Draft v0.1 · 2026-04-23
> **Owner:** Data Agent
> **Based on:** `../product/data-model.md` §4, B2 Phase 1 local filesystem

---

## 1. 設計原則

1. **DB 存 key，不存 URL**。URL 隨時由 StorageBackend 動態產生，換 backend 不影響 DB
2. **Key 格式穩定**，即使之後切 S3 / MinIO，key 語義不變
3. **權限控制不靠路徑難猜**，靠 backend 層 signed URL + ownership check
4. **單一 abstract interface**，本機 / S3 / MinIO 都實作同一組 method

---

## 2. Key 命名約定

所有 storage key 採用以下 path-like 格式：

```
characters/{character_id}/base.png
characters/{character_id}/aliases/{alias_id}.png
characters/{character_id}/motions/{motion_id}.mp4

checkpoints/{session_id}/{checkpoint_id}.png
checkpoints/{session_id}/references/{reference_id}.{ext}

exports/{character_id}/{export_id}.zip
```

### 設計理由

- **階層化**：方便 debug（本機 `ls` 就能看）、方便備份（按 character 分目錄 tar）
- **UUID-only 檔名**：不用使用者自訂，避免衝突、避免 filename injection
- **Extension 保留**：backend / frontend 可從 key 推斷 content-type

### 為什麼沒有 `users/{user_id}/` prefix

Phase 1 單 team，`character_id` 已經 globally unique，加 user 維度是冗餘。
Ownership 由 `characters.owner_id` 保證，不靠路徑結構。

未來 multi-team 時，key format 改為 `teams/{team_id}/characters/{character_id}/...`，走一次 migration 批次 rename。

---

## 3. StorageBackend Interface

抽象介面契約（Python，SQLAlchemy-adjacent）：

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import BinaryIO, Optional


@dataclass
class StoredObject:
    key: str              # 'characters/uuid/base.png'
    size_bytes: int
    content_type: str     # 'image/png'
    etag: str             # hash for integrity check
    created_at: datetime


class StorageBackend(ABC):
    """
    Abstract file storage. All paths are opaque 'key' strings.
    Backends: LocalFilesystemBackend, S3Backend (future), MinIOBackend (future).
    """

    @abstractmethod
    def put(
        self,
        key: str,
        content: bytes | BinaryIO,
        content_type: str,
    ) -> StoredObject:
        """Upload content at key. Overwrites if exists."""
        ...

    @abstractmethod
    def get(self, key: str) -> bytes:
        """Download content. Raises NotFoundError if missing."""
        ...

    @abstractmethod
    def get_stream(self, key: str) -> BinaryIO:
        """Stream download (for large motion videos)."""
        ...

    @abstractmethod
    def get_signed_url(
        self,
        key: str,
        expires_in_seconds: int = 3600,
    ) -> str:
        """
        Return a time-limited URL the client can use directly.
        Local backend: returns '/storage/{key}?token={signed_jwt}'
        S3 backend: returns S3 presigned URL.
        """
        ...

    @abstractmethod
    def delete(self, key: str) -> None:
        """Hard delete. Idempotent (no error if missing)."""
        ...

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Check existence without downloading."""
        ...

    @abstractmethod
    def list_prefix(self, prefix: str) -> list[StoredObject]:
        """List objects under a key prefix. For ZIP export enumeration."""
        ...

    @abstractmethod
    def copy(self, src_key: str, dst_key: str) -> StoredObject:
        """
        Server-side copy (no download+reupload).
        For Character Copy operation (B1).
        """
        ...
```

**Exception 類別：**

```python
class StorageError(Exception): ...
class NotFoundError(StorageError): ...
class AccessDeniedError(StorageError): ...
class StorageBackendUnavailableError(StorageError): ...
```

---

## 4. Phase 1 實作：`LocalFilesystemBackend`

### 4.1 檔案系統路徑

```
${STORAGE_ROOT}/
├── characters/
│   └── {character_id}/
│       ├── base.png
│       ├── aliases/
│       │   ├── {alias_id}.png
│       │   └── {alias_id}.png
│       └── motions/
│           ├── {motion_id}.mp4
│           └── {motion_id}.mp4
├── checkpoints/
│   └── {session_id}/
│       ├── {checkpoint_id}.png
│       └── references/
│           └── {reference_id}.jpg
└── exports/
    └── {character_id}/
        └── {export_id}.zip
```

環境變數：`STORAGE_ROOT=/var/character-foundry/storage`（DevOps 設定）。

### 4.2 Signed URL 策略（本機）

本機 backend 不能用 S3 presigned URL，改用 **application-level signed URL**：

```
GET /storage/{key}?token={signed_jwt}&expires={timestamp}
```

Backend 驗證 JWT：
- Payload 含 `{key, user_id, expires}`
- 簽章用服務端 secret
- 過期後 403

Frontend 拿到 signed URL 後可直接 `<img src>` / `<video src>`。

**ownership check 在簽發時做**：backend 發 signed URL 前，檢查 `request.user` 對該 key 對應的 entity 有讀取權限。過期的 URL 即使有效 signature 也拒絕。

### 4.3 Atomic Write 策略

避免部分寫入污染：

```python
def put(self, key, content, content_type):
    final_path = self._resolve(key)
    tmp_path = f"{final_path}.tmp.{uuid4()}"

    os.makedirs(os.path.dirname(final_path), exist_ok=True)

    with open(tmp_path, 'wb') as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())

    os.rename(tmp_path, final_path)  # POSIX atomic
    return StoredObject(...)
```

S3 backend 天生 atomic（single PUT operation）。

### 4.4 Copy 實作（B1 Character Copy）

本機：`os.link()`（hardlink）或 `shutil.copy2()`（複製檔案）。

建議 **hardlink**：
- 空間效率高（inode 共享）
- 讀取效能一樣
- 原始檔案刪除時 link 仍保留內容（不影響 Copy 來的 Character）

S3：`S3Client.copy_object()`（server-side copy，不耗頻寬）。

### 4.5 Fork checkpoint image copy（lifecycle 隔離）

`POST /v1/checkpoints/{id}/fork` 從現有 checkpoint 開新 Character + Session，新 session 的第一個 checkpoint 需要 source 的 image。

**規則：必須複製檔案到新 session namespace，不可共用 source key。**

理由：
- Source session 可能 `abandoned`，依 `lifecycle.md` 7 天後 cleanup 會 cascade-delete source checkpoints + 對應 storage 檔
- Source checkpoint 對 fork 沒有 FK 保護（不像 `bases.from_checkpoint_id` 有 `ON DELETE RESTRICT`）
- 共用 key 會讓新 character 的第一個 checkpoint 變 broken image

| Storage backend | 實作 |
|---|---|
| LocalFilesystemBackend | `os.link()` 優先（inode 共享、空間零成本、source 刪除 link 仍保留），不支援時 fallback `shutil.copy2()` |
| S3 | `S3Client.copy_object()` server-side copy |

**只複製 image 檔案**（含 `_thumb.png`）。`prompt`、`generation_log_id` 等 metadata 純 reference 共用，不會被 cleanup 影響。

對應 ticket：T-018（Sprint 2）。

---

## 5. 權限模型

### 5.1 讀取權限

| Entity | 可讀取者 | 驗證方式 |
|---|---|---|
| Character base/aliases/motions | 同 team 所有人 | Backend 查 `character.team_id == request.user.team_id` |
| Checkpoint images | 僅 session initiator | `session.initiator_id == request.user.id` |
| Export ZIP | 僅 character owner | `character.owner_id == request.user.id` |

### 5.2 寫入權限

| Entity | 可寫入者 | 驗證方式 |
|---|---|---|
| 新增 Character / Alias / Motion | Character owner | `character.owner_id == request.user.id` |
| Copy 產生的新 Character | 所有人 | 產生時 owner = request.user.id |
| Checkpoint | Session initiator | `session.initiator_id == request.user.id` |

Backend Agent 需實作 permission decorator / dependency injection。

---

## 6. ZIP Export Process

### 6.1 流程

```
1. User clicks Download
2. Backend creates ExportTask (async, write to DB)
3. Backend enumerates character's base + aliases + motions via list_prefix
4. Stream-zip files into {STORAGE_ROOT}/exports/{character_id}/{export_id}.zip
5. Generate manifest.json from DB state
6. Add manifest.json as first entry in ZIP
7. Mark ExportTask complete, notify user
8. User downloads via signed URL pointing to the ZIP
9. ZIP auto-deleted after 7 days (scheduled job)
```

### 6.2 `manifest.json` 生成

格式 per `../product/data-model.md` §3。Backend 從 DB 組出 JSON，不從檔案系統推斷。

### 6.3 大小估算

單一 Character 粗估：
- Base PNG: ~2 MB
- 5 Aliases × 2 MB = 10 MB
- Motions × 10 × 5 MB = 50 MB
- **ZIP 總量：~62 MB**（已壓縮影片不太壓得動，圖片影響小）

Phase 1 暫不做 streaming download（同步等打包完成）。

### 6.4 未來 S3 時的差異

S3 backend 可以用 `s3 cp --recursive` + zip in Lambda，或直接生成 presigned URL 讓 client 用 S3 multipart download。實作細節 Backend Agent 在 S3 migration 時處理。

---

## 7. Disk Space 管理

Phase 1 單機部署，需監控 `STORAGE_ROOT` 容量。

**DevOps Agent 需要：**
- 設定 disk usage alert（80% / 90% threshold）
- 確保 `STORAGE_ROOT` 是獨立 volume / partition

**Data Agent 建議保留空間估算：**
- 假設 50 users × 20 Characters × 60MB = **60 GB 基準**
- 加上 Checkpoints（每個 session 可能 5-20 張，每張 ~2MB）→ 再加 **20 GB**
- 加上 exports buffer → **10 GB**
- **Phase 1 建議至少 200 GB 磁碟空間**

---

## 8. 關聯文件

- `db-schema.md` — DB schema 與欄位說明
- `lifecycle.md` — 檔案刪除與封存策略
- `../product/data-model.md` §3 — `manifest.json` 格式
- `../backend/CLAUDE.md` — Backend Agent 實作 StorageBackend
- `../devops/CLAUDE.md` — DevOps 設定 `STORAGE_ROOT`、volume、backup
