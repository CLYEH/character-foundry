# T-031: Backend — Alias generation endpoint + worker

**Status:** TODO
**Sprint:** 3
**Est:** M (2h)
**Depends on:** T-013（task queue / SSE）、T-015（reconciler）、T-018（base 已有資料）、T-030（image2image / inpaint client）
**Related:** T-032（alias CRUD 讀本單寫的 row）、T-035（prompt-preview alias mode）、T-036（frontend 呼叫）

---

## Scope

把「從 Base 生 Alias」整條 task pipeline 串起來：API 接 request → 起 task → worker 跑 reconciler + gpt-image-2 → 寫 alias row + storage → SSE 回成品。Aliases **永遠從 Base 生**（不從其他 alias 衍生）。

**In scope:**
- 新 router file `api/app/api/routes/aliases.py`
  - `POST /v1/characters/{character_id}/aliases`
    - Body：`{ name, input_mode: 'text' | 'image' | 'inpaint' | 'mixed', freeform_note?, reference_image_ids?, mask? }`（per `api-shape.md` §5.3）
    - 驗 character 存在、有 base、是 owner（403 否則）
    - 驗 input_mode 與 payload 一致（例：`inpaint` 必有 mask；`image` 必有 reference_image_ids）
    - 至少要填一項：freeform_note OR reference_image_ids OR mask（per UX §4.2 互動規則）；空白 → `VALIDATION_EMPTY_INPUT`
    - 名稱重名（同 character 內已存在） → `CONFLICT_DUPLICATE_NAME`（同 character 內 unique，per data model 約定）
    - 入 task queue → 202 回 `{ task_id, alias_id }`
- Worker job `api/app/workers/jobs/create_alias.py`
  - 讀 base image bytes（storage backend）
  - 讀 reference images bytes（如有）、mask bytes（如有）
  - 過 reconciler（T-015）：mode='create_alias'，輸入 freeform_note + 平台 constraints + 「base 已是平台合規圖」hint
  - 依 input_mode dispatch 到 T-030 的 method：
    - `inpaint` (or 含 mask 的 mixed)：`edit_inpaint`
    - `image` / `mixed` 含 reference 不含 mask：`edit_image2image`
    - `text`：`edit_image2image` with empty references（純文字補述）
  - 寫 generation_log row、alias row（status `completed`）
  - 寫 alias image 到 storage：`aliases/{alias_id}.png` + `_thumb.png`（per `storage-layout.md`）
  - SSE result：`{ alias: AliasDTO }`
- 進度推送：reconciler done 0.3、model started 0.5、saved 0.95、done 1.0
- Cancel：worker 在每個 stage start 前檢查 `task.cancel_requested`，true → `cancelled`
- Idempotency：同 `task_id` 重啟 worker 不會重複建 alias（檢查 alias row 已存在 → 跳過）
- Permission：character owner 才可建 alias；其他人 403

**Not in scope:**
- Alias list / detail / patch / delete（T-032）
- Mask 前端組裝（frontend ticket）
- Custom motion 共用同 worker pattern（T-033 自己一份）
- UI 「Alias 永遠從 Base 生」的呈現（T-037 frontend）

---

## Planning refs

- `planning/backend/api-shape.md` §5.3 — Alias endpoint
- `planning/backend/ai-integration.md` — image edit dispatch 規則
- `planning/backend/prompt-reconciler.md` — `create_alias` mode 提示組合
- `planning/data/db-schema.md` §3.8 aliases、generation_log
- `planning/data/storage-layout.md` §aliases/
- `planning/product/functional-scope.md` §4.2 F-10 / F-11
- T-017 worker（`create_checkpoint.py`）為 worker pattern 對照

---

## Acceptance criteria

- [ ] POST text-only happy → task → completed → alias row + 圖檔存在
- [ ] POST 含 mask（inpaint）happy → 走 `edit_inpaint`
- [ ] POST 含 reference images → 走 `edit_image2image`
- [ ] 名稱衝突 → 409 `CONFLICT_DUPLICATE_NAME`
- [ ] 三項都空 → 422 `VALIDATION_EMPTY_INPUT`
- [ ] Non-owner POST → 403
- [ ] Character 沒 base → 409 `CONFLICT_BASE_NOT_SET`
- [ ] Cancel running task → status=cancelled，alias row 不寫入（或寫成 cancelled，per checkpoint pattern）
- [ ] SSE 收到 progress events + 最終 result 含 AliasDTO
- [ ] `pytest api/tests/aliases/` 全綠
- [ ] OpenAPI 正確產出

---

## Files expected to touch

- `api/app/api/routes/aliases.py` (new)
- `api/app/services/alias_service.py` (new)
- `api/app/repositories/alias_repo.py` (new)
- `api/app/schemas/alias.py` (new) — request body + AliasDTO
- `api/app/workers/jobs/create_alias.py` (new)
- `api/app/workers/arq_worker.py` (edit) — register new job
- `api/app/main.py` (edit) — register router
- `api/tests/aliases/` (new) — 涵蓋上述 acceptance

---

## Notes

- `mask` payload 用 reference_image upload 同套機制：先 upload 拿 `mask_id`，再帶進 body？還是直接 base64？**選 upload pattern**（`POST /v1/characters/{id}/aliases/masks` 上傳 → 回 `mask_id`，body 帶 `mask: { mask_id }`）—— 與 reference_image_ids 對稱；同時讓 mask binary 不卡 JSON request body
  - 上傳端點本單做掉（小段，與 reference_images.py 同 pattern）
  - Mask 存 storage：`creation-sessions/{character_id}/masks/{mask_id}.png`
- Reconciler 的 `create_alias` mode：把 base 視為已合規圖，補述只負責「目標變化」，不重新注入 transparent bg / centered 等 constraints（會干擾 alias 編修）—— 這條由 reconciler 模組（T-015 升級）內建
- `Alias.input_mode` 持久化原始 mode（給 frontend 顯示「這個 alias 怎麼來的」）
- Soft delete 由 T-032 做；本單 alias row 寫 `deleted_at IS NULL`
- 路徑：`/v1/characters/{character_id}/aliases` 與 `/v1/aliases/{alias_id}/...`（T-032）並存——遵從 api-shape §5.3
