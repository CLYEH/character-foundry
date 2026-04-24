# T-017: Backend — Checkpoint generation flow (reference upload + enqueue + worker)

**Status:** TODO
**Sprint:** 2
**Est:** M (2h)
**Depends on:** T-005 (StorageBackend), T-013 (task queue), T-014 (AI client), T-015 (reconciler), T-016 (creation sessions)
**Related:** T-018（select-base 用 checkpoint output）、T-022（frontend SSE 消費者）

---

## Scope

串起 Creation Session 的核心生成動作：上傳參考圖 → 發起 checkpoint → arq worker 呼 gpt-image-2 → 存檔 → 更新 checkpoint + task。

**In scope:**
- `POST /v1/creation-sessions/{id}/reference-images` — multipart 上傳，驗 MIME（PNG/JPEG/WebP）、size ≤ 10MB，存 `StorageBackend`，建 `reference_images` row，回 `{ reference_image_id, url }`
- `POST /v1/creation-sessions/{id}/checkpoints` — body：
  ```
  {
    mode: 'retry_same' | 'remix' | 'fresh',
    base_checkpoint_id: UUID | null,   # mode=remix
    menu_selections: dict | null,
    freeform_note: str | null,
    reference_image_ids: [UUID] | null
  }
  ```
  - Validate mode combinations（retry_same 必須有 `base_checkpoint_id`；remix 也是；fresh 禁帶 base）
  - **不先建** `checkpoints` row（`output_image_key TEXT NOT NULL` 強制先有產物；見 db-schema §3.5）；lifecycle state 由 `tasks.status` 承載，SSE 已覆蓋全流程
  - 預先 reserve UUID v4 作為 `checkpoint_id`，並決定 `sequence = COUNT(checkpoints WHERE session_id=?) + 1`；兩者都塞進 `input_payload`（worker 成功時用同一個 UUID 寫 row；同 session 併發請求用 row lock 破 sequence tie）
  - 透過 T-013 `TaskService.create_task('create_checkpoint', input_payload={session_id, checkpoint_id, mode, sequence, ...})` 排 arq job
  - 回 `{ task_id, checkpoint_id }`（對齊 `api-shape.md` §5.2 合約；checkpoint_id 在 worker 完成前查 `GET /checkpoints/{id}` 會 404，但 UI 走 task SSE 不會直接打那個 endpoint）
- arq job handler `run_create_checkpoint(ctx, task_id)`：
  1. Mark task running，開 `progress_publisher` loop
  2. 根據 `input_mode`（來自 session）決定 text2image 或 image2image
  3. 呼 `PromptReconciler.reconcile(...)` 拿 final prompt
  4. 呼 `GptImage2Client.generate_*(prompt, reference_images=?)` 拿 bytes
  5. 寫 `StorageBackend`（key 格式：`creation-sessions/{session_id}/checkpoints/{ckpt_id}.png`）
  6. 寫 thumbnail（512w，PIL resize）存 `.../thumb.png`
  7. 寫 checkpoint row：`id`=預先 reserve 的 UUID、`output_image_key`=storage key（**不是** signed URL；signed URL 在讀取時由 `Checkpoint` DTO 動態產，見 db-schema §3.5、storage-layout.md）、`prompt`=完整英文 prompt、`generation_log` JSON
  8. 更新 task `completed`，result = Checkpoint DTO
  9. 全程錯誤包成 `AgentError` 寫進 `task.error`
- `Checkpoint.prompt_summary` 組：menu 摘要（「女性・大眼・黑長髮・水墨風」）+ freeform 前 80 字 + `...`（UX 規則）
- Cancel 支援：worker 每個階段結尾檢查 `task.cancel_requested`，若 true 則中止，**只更新 task.status=cancelled**，checkpoint row 根本不寫（沒 output_image_key 違反 NOT NULL）
- Integration test：完整走 stub AI client → 確認 task 到 completed、checkpoint row 有 output、storage 檔案存在

**Not in scope:**
- Inpaint mask（Sprint 3，Alias 才用）
- Veo motion 生成
- Reference image 刪除 / 清理（保留到 session 過期一起砍）

---

## Planning refs

- `planning/backend/api-shape.md` §5.2 — session endpoints
- `planning/backend/task-queue.md` §3.3 — worker 樣板程式碼
- `planning/backend/ai-integration.md` — gpt-image-2 呼叫
- `planning/data/db-schema.md` §3.5, §3.6 — checkpoints / reference_images tables
- `planning/data/storage-layout.md` — 路徑規則
- `planning/product/functional-scope.md` §4.1 F-04
- `planning/ux/user-flows.md` §4.1 Flow A, §6 row 4（prompt_summary）

---

## Acceptance criteria

- [ ] 上傳參考圖 → 回 reference_image_id，檔案寫到 storage backend
- [ ] `fresh` mode 無參考圖 → text2image；有參考圖 → image2image
- [ ] `remix` mode 會用 `base_checkpoint_id` 的輸出圖當 image conditioning
- [ ] `retry_same` mode 重用同 prompt + 不同 seed，產新 checkpoint row
- [ ] Worker 完成後：task=completed、checkpoint row 寫入（`output_image_key` 是 storage path 不是 URL）、thumbnail 存在、Checkpoint DTO 經 signed-URL 轉換後可被前端讀取
- [ ] AI stub mode 下整條 pipeline 可跑（CI 不需 OPENAI_API_KEY）
- [ ] Cancel：queued / running 狀態 cancel → task=cancelled，**無 checkpoint row 被寫入**（lifecycle 全在 task 上）
- [ ] 錯誤：reconciler fail / gpt-image-2 fail → task=failed + AgentError，**無 checkpoint row 被寫入**
- [ ] `pytest api/tests/checkpoints/` 含 integration test（FastAPI TestClient + 真 Redis/arq in-process worker）全綠

---

## Files expected to touch

- `api/app/routers/creation_sessions.py` (edit) — 加 endpoints
- `api/app/routers/reference_images.py` (new)
- `api/app/services/checkpoint_service.py` (new)
- `api/app/repositories/checkpoint_repo.py` (new)
- `api/app/repositories/reference_image_repo.py` (new)
- `api/app/workers/jobs/create_checkpoint.py` (new)
- `api/app/workers/arq_worker.py` (edit) — register job
- `api/app/schemas/checkpoint.py` (new)
- `api/app/utils/thumbnails.py` (new) — PIL helper
- `api/app/utils/prompt_summary.py` (new)
- `api/tests/checkpoints/` (new)
- `api/pyproject.toml` (edit) — `Pillow`

---

## Notes

- Thumbnails 跟 full image 同步產；若 PIL fail 不阻斷 task（log warning，thumbnail_url=null）
- `sequence` 用 Postgres `SELECT COUNT + 1 FOR UPDATE` 或 `INSERT ... RETURNING sequence FROM generate_series`；避免 race 用 row lock
- Checkpoint row 只在 worker 成功產出 image 後才寫（schema 要求 `output_image_key NOT NULL`；api-shape §6.7 Checkpoint DTO 也無 status 欄位）。Enqueue 失敗 / worker 失敗 → 只有 task 記錄，前端從 `tasks.status` + `task.error` 得知
- Storage key 不曝露給 client，client 拿的是 signed URL（由 `StorageBackend.signed_url()` 產）
- Generation log JSON schema 對齊 `planning/data/db-schema.md §3.5`：`{ model, model_version, prompt, duration_ms, cost_units, seed? }`
- Integration test 用 `arq.testing.ArqRedis` + `fakeredis` 可 in-process 跑 worker
