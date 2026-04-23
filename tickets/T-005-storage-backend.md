# T-005: StorageBackend Interface + LocalFilesystemBackend

**Status:** TODO
**Sprint:** 0
**Est:** M (2h)
**Depends on:** T-001, T-002
**Related:** T-014+（Character 建立時會寫檔）

---

## Scope

實作 `StorageBackend` 抽象介面 + `LocalFilesystemBackend` 實作，讓後續 feature 可以存圖 / 影片 / ZIP 而不綁死本機。

**In scope:**
- `StorageBackend` abstract class（Python ABC）
- `StoredObject` dataclass
- `LocalFilesystemBackend` 實作：
  - `put` atomic write（.tmp + rename）
  - `get` / `get_stream`
  - `get_signed_url` JWT-based（`/storage/{key}?token=...`）
  - `delete`（idempotent）
  - `exists`
  - `list_prefix`
  - `copy`（`os.link` hardlink 優先，失敗 fallback 到 `shutil.copy2`）
- FastAPI `/storage/{key:path}` endpoint 驗 JWT 後 stream 檔案
- Storage exception hierarchy（`NotFoundError`, `AccessDeniedError`, `StorageError`, `StorageBackendUnavailableError`）
- DI hook：`get_storage() -> StorageBackend` FastAPI dependency
- Unit tests（tmp dir + sample file 測完整 CRUD + copy）

**Not in scope:**
- S3Backend / MinIOBackend（Phase 2）
- Orphan reconciliation job（後續）
- ZIP export 邏輯（feature 單）

---

## Planning refs

- `planning/data/storage-layout.md` §3 Interface 契約
- `planning/data/storage-layout.md` §4 LocalFilesystemBackend
- `planning/backend/api-shape.md` §5.8 Signed URL serving
- `planning/backend/api-shape.md` §4.1 Error categories — `STORAGE_*`
- FB-3: `STORAGE_URL_EXPIRED` 跟 `AUTH_INVALID_TOKEN` 要區分

---

## Acceptance criteria

- [ ] `pytest api/tests/storage/test_local_backend.py` 綠
- [ ] 單元測試涵蓋：put / get / delete / exists / list_prefix / copy / atomic rollback / signed URL 驗證（valid / expired / tampered 三種）
- [ ] `GET /storage/{key}?token={valid_jwt}` 回 binary + 正確 content-type
- [ ] `GET /storage/{key}?token={expired_jwt}` 回 403 + `AgentError { code: 'STORAGE_URL_EXPIRED', retryable: true }`
- [ ] `GET /storage/{key}?token={tampered}` 回 403 + `AgentError { code: 'AUTH_INVALID_TOKEN', retryable: false }`
- [ ] `storage.copy(src, dst)` 在 Linux 用 hardlink（`stat -c '%h'` > 1）

---

## Files expected to touch

- `api/app/storage/__init__.py` (new)
- `api/app/storage/backend.py` (new) — `StorageBackend` ABC + `StoredObject`
- `api/app/storage/local.py` (new) — `LocalFilesystemBackend`
- `api/app/storage/signed_url.py` (new) — JWT 簽 / 驗 signed URL
- `api/app/storage/errors.py` (new)
- `api/app/api/routes/storage.py` (new) — `/storage/{key:path}`
- `api/app/api/deps.py` (edit) — 加 `get_storage()` dependency
- `api/app/core/errors.py` (new，若還沒有) — `AgentError` base class（之後 T-006 會擴充）
- `api/tests/storage/test_local_backend.py` (new)
- `api/tests/storage/test_signed_url.py` (new)
- `api/tests/storage/test_storage_route.py` (new)

---

## Notes

- Signed URL JWT 用 `STORAGE_SIGNED_URL_SECRET`（跟 `JWT_SECRET` 獨立，避免互相污染）
- JWT payload 應含：`key`, `user_id`（之後做 ownership check）, `exp`
- 本單**不做** ownership check（user_id 先放進 payload，T-006 做 auth 後串起來）
- Content-type 推斷用 `mimetypes.guess_type`，支援 png / jpeg / mp4 / webp / zip
- `STORAGE_ROOT` 從 env var 讀，預設 `/storage`（容器內 path）
- Large file stream 用 FastAPI `StreamingResponse` + chunked read（避免 i2v 影片一次載入記憶體）
