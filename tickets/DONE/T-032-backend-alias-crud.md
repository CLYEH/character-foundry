# T-032: Backend — Alias list / detail / rename / delete

**Status:** TODO
**Sprint:** 3
**Est:** S (1h)
**Depends on:** T-031（alias model 已寫入；schemas 已建）
**Related:** T-037（frontend 用本單列出 / 刪除）；T-033（cascade-delete motions 規則由本單 delete 觸發）

---

## Scope

把 alias 的純 CRUD endpoint 補完。生成由 T-031 走 task；本單只處理同步的列表 / 讀取 / 改名 / 軟刪。

**In scope:**
- `GET /v1/characters/{character_id}/aliases`
  - 回 `{ items: [Alias] }`（per api-shape §5.3）
  - 排序：`created_at ASC`（與 owner 直觀「先建的在前」對齊）
  - 不分頁（Phase 1 一個 character 不會有上百 alias）
- `GET /v1/aliases/{alias_id}`
  - 回 `{ alias: AliasDetail }`，多帶 `motion_count` 與 `generation` subset（per api-shape §6.4）
- `PATCH /v1/aliases/{alias_id}`
  - Body: `{ name }`
  - 驗 same-character 內名字 unique → `CONFLICT_DUPLICATE_NAME`
- `DELETE /v1/aliases/{alias_id}`
  - Soft delete（`deleted_at = NOW()`）
  - **Cascade soft-delete 該 alias 底下所有 motions**（per F-12）
  - 回 204
- 全部 endpoint owner-only（403 否則）
- 全部 endpoint 對 `deleted_at IS NOT NULL` 的 row 視為不存在（404）
- `CharacterDetail.aliases` 序列化也要把 deleted alias 排除（沿用 character_repo 既有 join）

**Not in scope:**
- Restore 已刪 alias（Phase 1 不做）
- Hard delete cleanup（Sprint 5）
- Frontend 串接（T-037）

---

## Planning refs

- `planning/backend/api-shape.md` §5.3 — Alias endpoints
- `planning/data/db-schema.md` §3.8 aliases、§3.9 motions
- `planning/data/lifecycle.md` — soft delete 約定
- `planning/product/functional-scope.md` §4.2 F-10..F-12

---

## Acceptance criteria

- [ ] GET list 排除已刪、排序 `created_at ASC`
- [ ] GET detail 含 `motion_count`
- [ ] PATCH 改名同 character 重名 → 409
- [ ] DELETE 軟刪 + cascade 軟刪所有 motions（DB 上 motion.deleted_at 都被填）
- [ ] 已刪 alias 再 GET / PATCH / DELETE → 404
- [ ] Non-owner → 403
- [ ] `CharacterDetail.aliases` 不含已刪 alias
- [ ] OpenAPI 正確產出
- [ ] `pytest api/tests/aliases/test_crud.py` 全綠

---

## Files expected to touch

- `api/app/api/routes/aliases.py` (edit) — 加 list / detail / patch / delete
- `api/app/services/alias_service.py` (edit) — 業務邏輯（cascade delete）
- `api/app/repositories/alias_repo.py` (edit)
- `api/app/repositories/character_repo.py` (edit) — detail 序列化排除已刪 alias
- `api/app/schemas/alias.py` (edit) — `AliasDetail` schema
- `api/tests/aliases/test_crud.py` (new)

---

## Notes

- Cascade 軟刪：在 alias_service.delete 同 transaction 內也 update motions set `deleted_at=NOW()` where alias_id=… —— 不靠 DB FK cascade（FK cascade 會 hard delete）
- 改名不影響 storage path（image_key 用 alias_id，不用 name）
- 不在本單清理 storage 上的圖檔；Sprint 5 cleanup job 處理
