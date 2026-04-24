# T-018: Backend — Select Base / Fork checkpoint / Abandon session

**Status:** TODO
**Sprint:** 2
**Est:** S (1h)
**Depends on:** T-016, T-017
**Related:** T-025（frontend select-base UI）、T-026（E2E 走完整 flow）

---

## Scope

Checkpoint 生完之後 close the loop 的三個端點：確立 Base、fork 成新 character、放棄 session。完成這單 = Sprint 2 backend 能 end-to-end 建完 Character。

**In scope:**
- `POST /v1/creation-sessions/{id}/select-base { checkpoint_id }` →
  - 驗 checkpoint 屬於該 session 且 status=completed
  - 建 `bases` row：`character_id`、`from_checkpoint_id`、`image_key` = source checkpoint 的 `output_image_key`（同一個 storage path 共用，不另外複製檔案）；`image_embedding` 也可從 checkpoint 帶過來。**不寫** `image_url` / `generation_log` 欄位（schema 沒有；見 db-schema §3.6 bases 只有 image_key + image_embedding）
  - 更新 Character `base_id`、`updated_at`（**沒有** `thumbnail_url` 欄位 — characters 表不存縮圖；DTO 的 `base_thumbnail_url` 在讀取時透過 `bases.image_key` 推導 thumbnail signed URL）
  - 更新 Session `status=completed, completed_at=NOW()`
  - 將 checkpoint `selected_as_base=true`
  - 回 `{ character, base }`（Character 現在有 base 了）
  - Session 已 completed 再次呼叫 → 409 `CONFLICT_BASE_LOCKED`
- `POST /v1/checkpoints/{checkpoint_id}/fork { new_character_name }` →
  - 驗 checkpoint 存在 + status=completed
  - 建新 Character + 新 CreationSession（status=in_progress）
  - 新 session 裡塞第一個 checkpoint（sequence=1）複製自 source checkpoint（`output_image_key`、`prompt`、`generation_log_id` 共用 reference；signed URL 不存 DB，由 DTO 動態產）
  - 回 `{ character, creation_session }`
  - 新 Character 的 `copied_from_character_id` 不設（fork 是不同語義，由 copy 專用 flow 填）
- `POST /v1/creation-sessions/{id}/abandon` →
  - Session status 改 `abandoned`
  - Checkpoints 保留 **7 天**後由 scheduled job cascade 刪（對齊 `planning/data/lifecycle.md` line 63、`api-shape.md` §5.2）；scheduled job 本身在 Sprint 5 才實作，Sprint 2 只標記 status=abandoned
  - 204 No Content
- Permission：session owner == Character owner 才能動；其他人 403
- 測試：select-base happy path、重試 select 已鎖 → 409、fork 後新 char 不影響原 char、abandon 後 checkpoint endpoint 仍能讀

**Not in scope:**
- Abandoned session 的 scheduled cleanup（Sprint 5 polish）
- Base 重選（Phase 1 Base 不可變，要改只能刪 Character 重建）

---

## Planning refs

- `planning/backend/api-shape.md` §5.2 — select-base、fork、abandon 合約
- `planning/data/db-schema.md` §3.4, §3.5, §3.7 — bases / checkpoints / creation_sessions
- `planning/data/lifecycle.md` — session lifecycle（in_progress → completed/abandoned）
- `planning/product/functional-scope.md` §4.1 F-05, F-04

---

## Acceptance criteria

- [ ] Select-base happy：character.base_id 被設、session.status=completed、checkpoint.selected_as_base=true、bases row 寫入
- [ ] 再次 select-base 同 session → 409 `CONFLICT_BASE_LOCKED`
- [ ] Fork 產生新 character（新 owner = 呼叫者）+ 新 session + 第一 checkpoint 內容同源
- [ ] Fork 後原 character / session 完全無動（DB diff 為零）
- [ ] Abandon 把 session 狀態改對；再 POST checkpoints 回 409 `CONFLICT_SESSION_NOT_ACTIVE`
- [ ] Non-owner 呼叫任三個 endpoint → 403
- [ ] OpenAPI 正確產出
- [ ] `pytest api/tests/select_base/`、`api/tests/fork/`、`api/tests/abandon/` 全綠

---

## Files expected to touch

- `api/app/routers/creation_sessions.py` (edit) — 加 select-base / abandon
- `api/app/routers/checkpoints.py` (new) — fork endpoint
- `api/app/services/base_service.py` (new)
- `api/app/services/fork_service.py` (new)
- `api/app/repositories/base_repo.py` (new)
- `api/app/schemas/base.py` (new)
- `api/app/main.py` (edit)
- `api/tests/select_base/`、`api/tests/fork/`、`api/tests/abandon/` (new)

---

## Notes

- Select-base 是**同步**動作（只是 DB 寫入 + 複製 URL），不走 task queue
- Fork 同樣同步；Character DTO 會告知 frontend 跳到新 session
- Character 列表 / detail DTO 的 `base_thumbnail_url` 由 backend 在 serialize 時根據 `bases.image_key` 動態產 signed URL（thumbnail 路徑用 storage-layout 的 `_thumb.png` 約定），無需 DB 欄位
- Abandon 後 checkpoint 仍可被 fork（這是刻意 — 讓「先放著後再回來做」可行）
- 整個 operation 要放同一個 DB transaction（select-base 寫 3 張 table，一致性重要）
