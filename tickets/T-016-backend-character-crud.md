# T-016: Backend — Character CRUD + CreationSession bootstrap

**Status:** TODO
**Sprint:** 2
**Est:** M (2h)
**Depends on:** T-003 (characters / creation_sessions migrations), T-006 (auth)
**Related:** T-017（checkpoint 生成掛在 session 上）、T-020 / T-021 / T-025（frontend 吃這組 API）

---

## Scope

Character 與 CreationSession 的同步 CRUD。不含任何非同步 AI 生成（那是 T-017）。建立 endpoint、Pydantic schema、repository、service、permission 檢查（owner 與 team）。

**In scope:**
- `GET /v1/characters` — list，支援 `?owner_id=me|{uuid}&q={search}&limit&cursor`；預設 sort `updated_at DESC`
- `POST /v1/characters` — body `{ name, input_mode: 'template'|'reference' }` → 建 Character（尚無 base）+ 建 CreationSession（status=in_progress），回 `{ character, creation_session }`
- `GET /v1/characters/{id}` — `CharacterDetail`（Base / Aliases / Motions summary；Sprint 2 只會有 name / owner / thumbnail placeholder，base 欄位先用 null 直到 T-018 的 select-base）
- `PATCH /v1/characters/{id}` — 改名（owner only）
- `DELETE /v1/characters/{id}` — soft delete（set `deleted_at`）
- `POST /v1/characters/{id}/restore` — 30 天內反悔
- `GET /v1/creation-sessions/{session_id}` — 回 session + checkpoints（Sprint 2 此時 checkpoints 還空；T-017 才會塞 row）
- Permission：team 內可看，non-owner 不能 PATCH / DELETE（回 `AUTH_INSUFFICIENT_PERMISSION`）
- Validation：name 1–40 字、同 owner 下不可重名（回 `CONFLICT_DUPLICATE_NAME`）
- Slug 自動生成，依 `planning/data/db-schema.md` §4 演算法：pinyin(Chinese) → 小寫 → 連字號正規化 → 截 60 字 → `(owner_id, slug)` 衝突則 append `-2`, `-3`, ...（最多 100 次）→ 仍衝突才加 UUID prefix 4 碼
- Thumbnail URL：Sprint 2 先回 null；T-018 完成 select-base 後 backfill
- 單元 + integration tests

**Not in scope:**
- Checkpoint 生成（T-017）
- Select-base / Fork / Abandon（T-018）
- Copy character（Sprint 4）
- Export ZIP（Sprint 4）

---

## Planning refs

- `planning/backend/api-shape.md` §5.1, §5.2 — endpoint 合約
- `planning/backend/api-shape.md` §6.1, §6.2, §6.8 — `Character` / `CharacterDetail` / `CreationSession` DTO
- `planning/data/db-schema.md` §3.3, §3.4, §3.7 — characters / creation_sessions tables
- `planning/product/functional-scope.md` §4.1 F-01, F-06 — 建立流程與命名
- `planning/ux/user-flows.md` §6 — UX 已給 sort / motions_summary 決定
- `DECISIONS.md` §5 — 資料模型核心概念

---

## Acceptance criteria

- [ ] `POST /v1/characters {name:"阿雅",input_mode:"template"}` → 201，回 character + creation_session，DB 兩 row 同時建出
- [ ] 同 owner 重名 → 409 `CONFLICT_DUPLICATE_NAME`
- [ ] `GET /v1/characters?owner_id=me` 回本人角色，按 `updated_at DESC` 排序
- [ ] Cursor pagination：`next_cursor` 可帶回去再查下一頁
- [ ] Team 其他人可 GET list 與 detail，但 PATCH / DELETE 回 403
- [ ] Soft delete 後 list 不顯示，`/restore` 30 天內可救回、31 天後 410 `NOT_FOUND_CHARACTER`
- [ ] `GET /v1/creation-sessions/{id}` 回 session + 空 checkpoints 陣列
- [ ] OpenAPI schema 正確產出所有端點（`/openapi.json` 含 AgentError 錯誤回應）
- [ ] `pytest api/tests/characters/` + `api/tests/creation_sessions/` 全綠

---

## Files expected to touch

- `api/app/routers/characters.py` (new)
- `api/app/routers/creation_sessions.py` (new)
- `api/app/services/character_service.py` (new)
- `api/app/services/creation_session_service.py` (new)
- `api/app/repositories/character_repo.py` (new)
- `api/app/repositories/creation_session_repo.py` (new)
- `api/app/schemas/character.py` (new) — `Character`、`CharacterDetail`、`CreateCharacterRequest`
- `api/app/schemas/creation_session.py` (new)
- `api/app/utils/slug.py` (new) — pinyin slug + collision suffix
- `api/app/main.py` (edit)
- `api/tests/characters/`、`api/tests/creation_sessions/` (new)
- `api/pyproject.toml` (edit) — `pypinyin`

---

## Notes

- Slug collision：依 db-schema §4 — 先 `-2`, `-3`, ... 最多 100 次，超過才加 UUID prefix 4 碼（**不是** 直接用 hex suffix）
- Cursor 用 `(updated_at, id)` 複合；`updated_at` 同秒時用 id 破 tie
- `CharacterDetail.base` Sprint 2 結尾在 T-018 才有值；本單先 return null 並讓 Pydantic 接受 optional
- Delete 要連帶 soft delete creation_sessions？暫時不連動，保持 session 可單獨看（若 character 已刪，session 不對外出，但 fork 還能運作）— 這裡先不做複雜聯級，T-018 時再補
- `motions_summary.base.preset_generated` 預設 0；Sprint 3 才會 > 0
- Permission decorator `@require_character_owner` 放 `api/app/core/permissions.py`（若尚無檔就建）
