# T-028: Worker post-lock checkpoint guard

**Status:** TODO
**Sprint:** 2
**Est:** S (1–2h)
**Depends on:** T-017, T-018
**Related:** T-026（E2E 測試會撞到這個 race）

---

## Scope

讓 `run_create_checkpoint` worker 在寫 checkpoint row 前驗證 session 仍是 `in_progress`，避免 select-base / abandon 之後 inflight 的 task 還繼續寫進 terminal session。

**In scope:**
- Worker INSERT path 前先 `SELECT status FROM creation_sessions WHERE id=:s` 並 abort 若 != `in_progress`
- Abort 路徑：清掉已上傳的 storage outputs（reuse T-017 的 orphan-cleanup 分支），把 task 標 `cancelled`（不是 failed — 這是 user-initiated termination）
- 新增 worker 測試：concurrent select-base + worker 同時跑、post-abandon worker 不寫 row
- （optional）`SELECT ... FOR UPDATE` 跟 select-base / abandon 的鎖一致，避免 read-then-write race

**Not in scope:**
- Select-base 主動取消已 queued 的 task（替代方案；本單只走「worker 自己 revalidate」路線，避免 select-base service 跨進 task queue 領域）
- 同樣的 race 在 alias/motion worker（Sprint 3 才有，到時補）

---

## Planning refs

- `planning/backend/task-queue.md` §3.5 — Sequence race + worker idempotency
- `planning/data/lifecycle.md` — Session lifecycle states

---

## Acceptance criteria

- [ ] User queue 多個 checkpoint → 選 base → 後續 worker 不寫新 row（選了 base 之後 session 的 checkpoint count 凍住）
- [ ] User queue checkpoint → abandon session → worker 不寫 row（task 標 cancelled）
- [ ] Race scenarios：select-base 跟 worker 同時 → 兩者最多有一個贏；worker 輸了乾淨地 abort（無孤兒檔、無孤兒 row）
- [ ] `pytest api/tests/checkpoints/test_create_checkpoint_worker.py` 全綠（含新增 race tests）

---

## Files expected to touch

- `api/app/workers/jobs/create_checkpoint.py` (edit) — pre-INSERT status guard
- `api/app/repositories/creation_session_repo.py` (edit) — `get_for_update` 若採 SELECT FOR UPDATE 路線（也可重用 T-018 補的 helper）
- `api/tests/checkpoints/test_create_checkpoint_worker.py` (edit) — 新增 race + post-lock tests

---

## Notes

- Codex 在 PR #23（T-018）round-2 P1 提的 issue。當時判斷是 worker-path 改動超出 T-018「3 個 endpoint」的 scope，拆成這單獨立做。
- 「Worker 自己 revalidate」vs「select-base 主動取消 task」：前者只動 worker，鎖定範圍小；後者要跨 task queue + 處理 in-flight task 的時序，複雜很多。先做前者。
- Phase 1 量小，這個 race 實際 trigger 機率低，但 contract 得撐住（"completed means locked"）。
