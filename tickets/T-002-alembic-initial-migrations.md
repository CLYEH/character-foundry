# T-002: Alembic + Initial Migrations (teams, users, characters skeleton)

**Status:** TODO
**Sprint:** 0
**Est:** S (1h)
**Depends on:** T-001
**Related:** T-003, T-006

---

## Scope

接上 T-001，加入 Alembic 並跑完頭三個 migration：extensions、teams、users、characters skeleton（無 base FK）。

**In scope:**
- `api/alembic.ini` + `api/alembic/env.py`
- SQLAlchemy base + session 基礎（async engine、`get_db()` dependency）
- Migration 001：Extensions（uuid-ossp、pgcrypto、vector、pg_trgm）
- Migration 002：`teams` 表 + bootstrap insert default team
- Migration 003：`users` 表
- Migration 004：`characters` skeleton（base_id / creation_session_id 欄位留著但無 FK，FK 在 T-003 的後續 migration 加）
- 自動更新 `updated_at` 的通用 trigger function

**Not in scope:**
- 其他資料表（creation_sessions、checkpoints、bases、aliases、motions、generation_logs、tasks）→ T-003
- 任何 API endpoint（T-006 起）

---

## Planning refs

- `planning/data/db-schema.md` §1 技術棧、§2 刪除策略、§3.1-3.3 對應表、§7 Migration 順序
- `planning/data/storage-layout.md` — 對本單無直接影響，但之後 T-005 會用到

---

## Acceptance criteria

- [ ] `alembic upgrade head` 在乾淨 DB 執行成功
- [ ] Extensions 已裝：`SELECT extname FROM pg_extension` 含 `vector`, `pg_trgm`, `pgcrypto`
- [ ] `SELECT * FROM teams` 有一筆 `name='default'`
- [ ] `SELECT * FROM users WHERE 1=0` 能跑（schema 對）
- [ ] `SELECT * FROM characters WHERE 1=0` 能跑
- [ ] `alembic downgrade base` 可以乾淨回滾（無錯）
- [ ] `pytest api/tests/migrations/test_migrate.py` 綠（自動跑 up/down/up）

---

## Files expected to touch

- `api/alembic.ini` (new)
- `api/alembic/env.py` (new)
- `api/alembic/versions/20260423_001_extensions.py` (new)
- `api/alembic/versions/20260423_002_teams.py` (new)
- `api/alembic/versions/20260423_003_users.py` (new)
- `api/alembic/versions/20260423_004_characters_skeleton.py` (new)
- `api/app/db/__init__.py` (new)
- `api/app/db/base.py` (new) — declarative base
- `api/app/db/session.py` (new) — async session factory
- `api/app/models/__init__.py` (new)
- `api/app/models/team.py` (new)
- `api/app/models/user.py` (new)
- `api/app/models/character.py` (new) — skeleton
- `api/tests/migrations/test_migrate.py` (new)
- `api/pyproject.toml` (edit) — 加 alembic / SQLAlchemy / asyncpg / psycopg2-binary

---

## Notes

- Alembic env 用 **async** 模式（跟 FastAPI 配合）
- Characters name / slug 的 CHECK constraint 先加（詳見 db-schema.md §3.3）
- `base_id` 跟 `creation_session_id` 在此單**留 nullable 欄位但不建 FK**，避免循環依賴
- 所有表都採用 `gen_random_uuid()` 預設值
- `updated_at` trigger function 通用，後面其他表也會用
