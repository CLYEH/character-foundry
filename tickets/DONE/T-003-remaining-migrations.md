# T-003: Remaining Migrations (sessions, checkpoints, bases, aliases, motions, generation_logs, tasks)

**Status:** DONE
**Sprint:** 0
**Est:** M (2h)
**Depends on:** T-002
**Related:** T-005（storage 會寫 image_key 欄位）, T-006 起的 feature 單

## Implementation notes (2026-04-24)

- Skipped the **updated_at triggers migration**: per `planning/data/db-schema.md`, only `characters` carries an `updated_at` column and its trigger was already installed in `20260423_004_characters_skeleton.py`. No other Phase-1 table has `updated_at`, so there is nothing to wire up.
- Skipped the **optional `user_usage_summary` materialized view** per db-schema §3.10's own guidance ("Phase 1 先直接 query"). Re-open when `generation_logs` rows cross 100k.
- `generation_logs` bootstrap partitions: 2026-04 / 05 / 06. Subsequent months are the job of the scheduled partition-rotation task (see `planning/data/lifecycle.md` §4.2).
- Fixed a pre-existing local-dev blocker: `api/alembic.ini` contained an em dash that broke `configparser` on cp950 Windows locales. Replaced with ASCII.

---

## Scope

接上 T-002，完成剩餘的 migrations（總共 9 張表 + tasks + indexes + FK back-references）。Schema 完整可支撐後續所有 feature 實作。

**In scope:**
- Migration：`creation_sessions`（+ FK back to characters）
- Migration：`checkpoints`（含 `output_image_embedding vector(768)` + ivfflat index）
- Migration：`bases`（+ FK back to characters）
- Migration：`aliases`（含 embedding + index）
- Migration：`motions`（polymorphic: base_id / alias_id 二選一 CHECK）
- Migration：`generation_logs`（monthly partitioned，建 2026-04 + 2026-05 + 2026-06 三張 partition）
- Migration：`tasks`（per db-schema.md §3.11，含 4 個 CHECK constraints + 4 個 indexes）
- Migration：updated_at trigger 套用到 characters, creation_sessions
- Migration：（optional）`user_usage_summary` materialized view — 若使用者覺得太複雜可跳過

**Not in scope:**
- Scheduled jobs 實作（T-xxx 之後 sprint）
- Orphan reconciliation job（T-xxx 之後）
- Partition rotation job（T-xxx 之後）

---

## Planning refs

- `planning/data/db-schema.md` §3.4-§3.11 全部資料表 DDL
- `planning/data/db-schema.md` §5 Index 策略總覽
- `planning/data/db-schema.md` §7 Migration 順序
- `planning/data/lifecycle.md` §1 — 瞭解各表的生命週期（設 FK on delete 模式要對）

---

## Acceptance criteria

- [ ] `alembic upgrade head` 成功
- [ ] 所有 9 張 entity 表 + tasks + materialized view（若做）存在
- [ ] Polymorphic motions 的 CHECK 生效：跑 SQL `INSERT INTO motions(base_id, alias_id, ...) VALUES (NULL, NULL, ...)` 應 fail
- [ ] GenerationLog partition 至少 3 張（當月 + 次月 + 再次月）
- [ ] `\d+ tasks` 顯示 4 個 indexes 與所有 CHECK constraints
- [ ] `alembic downgrade base` 可乾淨回滾
- [ ] `pytest api/tests/migrations/` 綠

---

## Files expected to touch

- `api/alembic/versions/20260423_005_creation_sessions.py` (new)
- `api/alembic/versions/20260423_006_checkpoints.py` (new)
- `api/alembic/versions/20260423_007_bases.py` (new) — 含 character.base_id FK
- `api/alembic/versions/20260423_008_aliases.py` (new)
- `api/alembic/versions/20260423_009_motions.py` (new)
- `api/alembic/versions/20260423_010_generation_logs.py` (new)
- `api/alembic/versions/20260423_011_tasks.py` (new)
- `api/alembic/versions/20260423_012_triggers.py` (new)
- `api/alembic/versions/20260423_013_usage_summary.py` (optional)
- `api/app/models/creation_session.py` (new)
- `api/app/models/checkpoint.py` (new)
- `api/app/models/base.py` (new)
- `api/app/models/alias.py` (new)
- `api/app/models/motion.py` (new)
- `api/app/models/generation_log.py` (new)
- `api/app/models/task.py` (new)

---

## Notes

- pgvector 的 IVFFlat index 需要先有足夠資料才能好好建（Phase 1 資料少的話可先建空的，或延後到有資料再建）→ 建議建空 index，之後 `REINDEX` 即可
- Monthly partition 名稱格式：`generation_logs_YYYY_MM`
- Partition table 不支援被 FK 引用 → 其他表的 `generation_log_id` 欄位不建 FK（應用層保證一致性）
- Motions 的 polymorphic 用 Option B（exactly-one CHECK），**不用** trigger（更簡單）
- 若 materialized view 這輪不做，記得在 open questions 留記號（M5 風格）
