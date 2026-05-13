# T-055: `refresh_token` table 加 `token_source` 欄位

**Status:** TODO
**Sprint:** 3.5a
**Est:** XS
**Depends on:** none
**Related:** T-054（dual-stack middleware 會讀本欄位選 refresh path）

---

## Scope

`refresh_token` table 加 enum 欄位 `token_source ('jwt' | 'oauth')`，預設 `'jwt'`，既有資料一律 backfill 為 `'jwt'`（Phase 1 在本單 ship 之前所有 token 都是 JWT）。

per auth Q6 決策：重用既有 table + 加欄位（而非新表 `oauth_refresh_token`）。

**In scope:**
- Alembic migration：`add_token_source_to_refresh_token`
- SQLAlchemy model 加 `Enum` column
- 既有 `refresh_token` row backfill 為 `'jwt'`（用 `op.execute("UPDATE refresh_token SET token_source = 'jwt'")`)
- 既有 `create_refresh_token()` helper 增加 `token_source` 參數，default `'jwt'` 保持向後相容
- Tests：migration upgrade/downgrade、model round-trip、helper default

**Not in scope:**
- 利用本欄位選 refresh path（T-054）
- `oauth` source 的 refresh 流程實作（T-054）

---

## Planning refs

- `planning/auth/open-questions.md` Q6（重用 table + 加欄位）
- `planning/data/`（既有 `refresh_token` schema）

---

## Acceptance criteria

- [ ] `alembic upgrade head` 在 fresh DB 與 migrated DB 都成功
- [ ] `alembic downgrade -1` 把欄位安全移除（測試）
- [ ] Backfill：upgrade 後既有 row 的 `token_source` 都是 `'jwt'`
- [ ] `RefreshToken` SQLAlchemy model 暴露 `token_source` field，type 是 `RefreshTokenSource` enum
- [ ] `create_refresh_token()` helper 默認行為不變（既有 caller 不必改）
- [ ] `pytest api/tests/auth/test_refresh_token.py` 全綠（含新 enum 驗證）

---

## Files expected to touch

- `api/alembic/versions/XXXX_add_token_source_to_refresh_token.py` (new)
- `api/app/models/auth.py` (edit) — 加 `RefreshTokenSource` enum + column
- `api/app/auth/refresh.py`（or 等同檔案）(edit) — `create_refresh_token` 加 param
- `api/tests/auth/test_refresh_token.py` (edit) — 加 enum case
- `tickets/T-055-refresh-token-source-column.md` (new — 本單)
- `STATUS.md` (edit)

---

## OAuth scope required

`n/a`（DB migration，不開 endpoint）

---

## MCP tool delta

`n/a`

---

## Notes

- **Enum 在 DB 層**：用 postgres native enum（per 既有 model 慣例）。Migration 用 `sa.Enum('jwt', 'oauth', name='refresh_token_source')` + `create_type=True`，downgrade 補 `drop_type=True`
- **為什麼不用 string column**：enum 在 DB 層有 type safety、index 友善、未來新增值（例 `'service_account'`）走 alembic op 而非無校驗的 string
- **Backfill 必須在 migration 內做**：用 `op.execute("UPDATE refresh_token SET token_source = 'jwt' WHERE token_source IS NULL")` 在 add_column 後立即跑；最後 `alter_column(..., nullable=False)` 鎖緊
- **不在本單做的事**：本欄位的 read path（如何根據 `token_source` 選不同 refresh endpoint）落在 T-054，本單只負責 schema
