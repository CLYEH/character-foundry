# T-027: CharacterDetail DTO + frontend 恢復 in-progress session

**Status:** TODO
**Sprint:** 2
**Est:** S (1h)
**Depends on:** T-016 (CharacterDetail 已實作), T-025 (detail 頁已實作 inline error)
**Related:** T-025（取代 inline error 為 resume 按鈕）

---

## Scope

把 STATUS.md backlog 項目 S2-2 升 Sprint 2 ticket。

PR #14 review round 4 時，T-025 因 `CharacterDetail` DTO 沒 session_id 推不出 redirect target，改成 inline error 頂著 — 但這對「使用者建到一半離開後從 Dashboard / 直連 URL 回來」是 UX gap。本單把 DTO 加上 `creation_session` 欄位、前端用它做 resume 按鈕。

**In scope:**

**Backend (修改 T-016 已實作的 CharacterDetail serializer):**
- `GET /v1/characters/{id}` 回應的 `CharacterDetail` 新增 `creation_session: { id, status } | null` 欄位（合約定義見 `planning/backend/api-shape.md` §6.2）
- Serializer 規則（對齊 api-shape）：
  - `base !== null` → `creation_session = null`（不查 DB，節省 payload）
  - `base === null` → join `creation_sessions`，回 `{id, status}`，status 只有 `in_progress` 或 `abandoned`（completed 必有 base，不會落到這條路徑）
- DTO 同步補在 OpenAPI spec
- 單元測試：base 已確立 → null；in_progress session 存在 → 帶 id+status='in_progress'；abandoned → 帶 status='abandoned'

**Frontend (T-025 已實作 inline error 替換為 resume 流程):**
- `CharacterDetailPage` 在 `base === null` 時讀 `creation_session.status`：
  - `in_progress` → 顯示 hero card：「此角色尚未確立 Base」+ Primary CTA `[繼續建立]`（→ `navigate('/characters/new/session/{creation_session.id}')`）+ secondary Back to Dashboard
  - `abandoned` → 顯示「此 session 已被放棄」+ Back to Dashboard（不提供 resume — abandoned session 不該繼續）
  - `creation_session === null` 但 `base === null`（不該發生）→ fallback 原 inline error
- T-025 的 inline error 元件改成上述三狀態的 component
- Vitest：三種 case 各一個 spec

**清理:**
- STATUS.md 移除 backlog item S2-2（已升 ticket）
- T-025 ticket 加 cross-ref 到 T-027（本單 supersede 那邊的 inline error 規劃）

**Not in scope:**
- 後端 CreationSession 本身的功能（in T-016 / T-018，已存在）
- Resume session 內部繼續產 checkpoint 的 flow（在 T-022，已存在）

---

## Planning refs

- `planning/backend/api-shape.md` §6.2 — `CharacterDetail` DTO（已含 `creation_session` 欄位定義）
- `planning/data/db-schema.md` §3.3, §3.4 — characters / creation_sessions 表（join 來源）
- `planning/ux/user-flows.md` §4.1 Flow A — creation session 主 flow（resume 是這 flow 的 entry point alternative）

---

## Acceptance criteria

- [ ] `GET /v1/characters/{id}` Response 含 `creation_session` 欄位（base 已確立 → null；base null + session in_progress → `{id, status: 'in_progress'}`；base null + session abandoned → `{id, status: 'abandoned'}`）
- [ ] Backend integration test 三種 case 全綠
- [ ] Frontend：base 未確立 + session in_progress → Detail 頁顯示「繼續建立」CTA，點擊跳對應 session 頁
- [ ] Frontend：base 未確立 + session abandoned → 顯示放棄訊息，無 resume CTA
- [ ] Frontend：base 已確立 → 正常 detail（與 T-025 行為一致）
- [ ] OpenAPI schema 更新後 frontend 的 typed client 不需手動修
- [ ] STATUS.md S2-2 backlog 項移除

---

## Files expected to touch

- `api/app/schemas/character.py` (edit) — `CharacterDetail` 加 `creation_session` Pydantic field
- `api/app/services/character_service.py` (edit) — serializer join creation_sessions
- `api/app/repositories/character_repo.py` (edit) — query helper
- `api/tests/characters/test_character_detail.py` (edit) — 三種 case
- `web/src/routes/characters/[id]/CharacterDetailPage.tsx` (edit)
- `web/src/components/characters/IncompleteCharacterCard.tsx` (new) — 三狀態元件
- `web/src/routes/characters/[id]/__tests__/` (edit)
- `STATUS.md` (edit) — 移除 S2-2 backlog
- `tickets/T-025-frontend-select-base-character-detail.md` (edit) — 加 cross-ref note 指向 T-027

---

## Notes

- 一張 character 的 creation_session 是 1:1（schema `characters.creation_session_id`），所以查詢直接 join 即可，無 N+1 風險
- 為什麼不在 T-016 做：T-016 已 in-flight；本單刻意分出來避免 round 4 那種「ticket scope 沒涵蓋 DTO 變動」的混淆
- Resume CTA 應該醒目（hero card 而非 toast / banner）— 使用者直連 URL 落地後第一眼要看到下一步
- abandoned session 不能 resume，因為原 user-flows §4.1 的 abandon 流程預期 cleanup；如要支援應該另開 ticket（unabandon API）
