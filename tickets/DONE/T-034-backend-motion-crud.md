# T-034: Backend — Motion list / detail / rename / delete

**Status:** TODO
**Sprint:** 3
**Est:** S (1h)
**Depends on:** T-033（motion model 已建）
**Related:** T-038（frontend 列出 / 刪除 / 重新生成）

---

## Scope

Motion 純 CRUD，與 T-032 alias CRUD 結構對稱。

**In scope:**
- `GET /v1/bases/{base_id}/motions` — 回 `{ items: [Motion] }`
- `GET /v1/aliases/{alias_id}/motions` — 回 `{ items: [Motion] }`
- `GET /v1/motions/{motion_id}` — 回 `{ motion: MotionDetail }`，含 `description`、`generation` subset
- `PATCH /v1/motions/{motion_id}`
  - Body: `{ name }`
  - **Preset motion 不可改名** → 422 `VALIDATION_PRESET_RENAME_FORBIDDEN`（per api-shape §5.4 註解）
  - 同 parent 下重名 → 409 `CONFLICT_DUPLICATE_NAME`
- `DELETE /v1/motions/{motion_id}` — soft delete，204
- 列表排序：`created_at ASC`，但 preset 永遠擺前面（前端要顯示 5 個固定位置；preset 已生成的填位置，沒生成的由前端在 list 缺漏對位置補 `+`）
- 全部 endpoint owner-only

**Not in scope:**
- Hard delete cleanup（Sprint 5）
- Storage 檔案清理（Sprint 5）
- Frontend 串接（T-038）

---

## Planning refs

- `planning/backend/api-shape.md` §5.4 Motions endpoints + DTO 6.5
- `planning/data/db-schema.md` §3.9 motions
- `planning/product/functional-scope.md` §4.3 F-23

---

## Acceptance criteria

- [ ] GET list（base / alias）排除已刪、preset 在前
- [ ] GET detail 回 `description` 與 `generation` 摘要
- [ ] PATCH preset → 422 `VALIDATION_PRESET_RENAME_FORBIDDEN`
- [ ] PATCH custom 重名 → 409
- [ ] DELETE 軟刪
- [ ] Non-owner → 403
- [ ] 已刪 motion 再 GET → 404
- [ ] OpenAPI 正確產出
- [ ] `pytest api/tests/motions/test_crud.py` 全綠

---

## Files expected to touch

- `api/app/api/routes/motions.py` (edit) — 加 list / detail / patch / delete
- `api/app/services/motion_service.py` (edit)
- `api/app/repositories/motion_repo.py` (edit)
- `api/app/schemas/motion.py` (edit) — `MotionDetail` schema
- `api/tests/motions/test_crud.py` (new)

---

## Notes

- 「Preset 已生成過視為佔據位置」的判斷在 T-033 generation 端 enforce；本單只把資料 list 出來
- 未來「重新生成」的 UX 是建新 motion（user 可自己刪舊的）—— Phase 1 不做 atomic replace
- Owner check 透過 motion → parent (base / alias) → character → owner 解析；放在 service 層
