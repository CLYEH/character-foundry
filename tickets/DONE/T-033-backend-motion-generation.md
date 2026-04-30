# T-033: Backend — Motion generation endpoint + worker

**Status:** TODO
**Sprint:** 3
**Est:** M (2h)
**Depends on:** T-013（task queue / SSE）、T-015（reconciler）、T-018（base 已有資料）、T-029（Veo client）
**Related:** T-031（alias 完成才會有 alias-bound motion；alias-bound motion 在 T-031 merge 後才能完整測試）；T-034（CRUD 讀本單寫的 row）；T-038 / T-039（frontend）

---

## Scope

把「Base 或 Alias → Motion 影片」整條 task pipeline 串起來。Phase 1 motion type 二類：5 種 preset + custom。每個 motion 綁在一個 base 或一個 alias 底下，**不跨共享**（per F-22）。

**In scope:**
- 新 router `api/app/api/routes/motions.py` 含 4 個端點：
  - `POST /v1/bases/{base_id}/motions`
  - `POST /v1/aliases/{alias_id}/motions`
  - Body：`{ motion_type, name, description? }`（per api-shape §5.4）
    - `motion_type` enum：`preset_wave` / `preset_nod` / `preset_gesture` / `preset_happy` / `preset_idle` / `custom`
    - `description` 僅 `custom` 必填；preset 由 backend 內建模板（見 Notes）
  - 驗 parent 存在 + 是 character owner（403 否則）
  - 同 parent 下名稱重複 → 409 `CONFLICT_DUPLICATE_NAME`
  - 同 parent 下 preset 已生成過 → 409 `CONFLICT_PRESET_ALREADY_EXISTS`（per F-20「5 個固定位置」）
  - 入 task queue → 202 回 `{ task_id, motion_id }`
- Worker job `api/app/workers/jobs/create_motion.py`
  - 讀 parent image bytes（base.image_key 或 alias.image_key）
  - **Preset 模板**：讀 `api/app/prompt/preset_motions.yaml`（新檔，見 Notes）取對應英文 prompt；不過 reconciler
  - **Custom 模板**：description 過 reconciler（mode=`create_motion`，翻譯 + 注入 motion 必要 constraints 如 transparent bg / centered / facing camera）
  - 呼 T-029 的 `VeoClient.generate_i2v(image_bytes=parent_image, prompt=..., duration_seconds=None)`（duration 由模型決定 / 預設 ~3s）
  - 寫 video 到 storage：
    - Base 的 motion：`bases/{base_id}/motions/{motion_id}.mp4`
    - Alias 的 motion：`aliases/{alias_id}/motions/{motion_id}.mp4`
  - 同時抽第一幀存 `_thumb.png`（用 ffmpeg 或 PIL `imageio[ffmpeg]`）
  - 寫 motion row + generation_log
  - SSE result：`{ motion: MotionDTO }`
- 進度推送：reconciler done 0.2、model started 0.4、video downloaded 0.85、thumbnail done 0.95、saved 1.0
- Cancel：每個 stage 開頭檢查 `task.cancel_requested`
- Idempotency：同 task_id 重啟 worker 不會重建 motion

**Not in scope:**
- Motion list / detail / patch / delete（T-034）
- ZIP 匯出（Sprint 4）
- Lip sync（Phase 1 暫緩）
- Frontend 觸發（T-038 / T-039）

---

## Planning refs

- `planning/backend/api-shape.md` §5.4 Motions
- `planning/backend/ai-integration.md` §Veo
- `planning/backend/prompt-reconciler.md` — `create_motion` mode
- `planning/data/db-schema.md` §3.9 motions
- `planning/data/storage-layout.md` §motions/
- `planning/product/functional-scope.md` §4.3 F-20..F-23
- `DECISIONS.md` §3 — first/last frame 策略（已封裝在 T-029）
- T-031 worker pattern 對照

---

## Acceptance criteria

- [ ] POST 對 base 的 preset_wave happy → task → completed → motion row + 影片 + thumbnail 存在
- [ ] POST 對 alias 的 custom happy → reconciler 翻譯 + Veo → motion row + 影片
- [ ] 同 parent 同 preset 第二次 → 409 `CONFLICT_PRESET_ALREADY_EXISTS`
- [ ] 同 parent 重名 custom → 409 `CONFLICT_DUPLICATE_NAME`
- [ ] Custom 缺 description → 422
- [ ] Non-owner → 403
- [ ] Cancel running task → status=cancelled
- [ ] SSE result 含 MotionDTO（含 `parent.type`、`parent.id`、`video_url`、`thumbnail_url`、`duration_ms`）
- [ ] `pytest api/tests/motions/` 全綠
- [ ] OpenAPI 正確產出

---

## Files expected to touch

- `api/app/api/routes/motions.py` (new)
- `api/app/services/motion_service.py` (new)
- `api/app/repositories/motion_repo.py` (new)
- `api/app/schemas/motion.py` (new) — request body + MotionDTO
- `api/app/workers/jobs/create_motion.py` (new)
- `api/app/workers/arq_worker.py` (edit)
- `api/app/main.py` (edit) — register router
- `api/app/prompt/preset_motions.yaml` (new) — 5 種 preset 英文 prompt
- `api/app/prompt/reconciler_modes.py`（or 對應檔案，edit） — 加 `create_motion` mode
- `api/tests/motions/` (new)

---

## Notes

- Preset prompt 範例（YAML 起手式）：
  ```yaml
  preset_wave:
    prompt: "Subject waves hand in greeting, smooth and friendly motion, transparent background, centered, facing camera directly."
    target_duration_ms: 3000
  preset_nod:
    prompt: "Subject nods head with explanatory expression, slight gesture, transparent background, centered, facing camera directly."
    target_duration_ms: 3000
  # ... gesture / happy / idle 同 pattern
  ```
- Preset 不過 reconciler（已是英文且符合 constraints），節省成本與延遲
- Custom 過 reconciler 時注入「動作層級的 constraints」：保持 transparent bg、保持 character identity（first/last frame 已在 T-029 處理）、避免 lip sync 暗示（Phase 1 無音）
- `target_duration_ms` 純參考；實際 duration 由 Veo 回的 video metadata 決定，寫入 motion row
- `motion_type='custom'` 的 row 必有 `name` 與 `description`；preset 的 `description` 為 null、`name` 用 zh 對照（招手歡迎 / 點頭說明 / 手勢指引 / 開心回應 / 靜置待機，per F-20）
- Thumbnail 抽幀用 PIL + imageio[ffmpeg]（Phase 1 deps 已在 T-014 加；若沒有，本單補進 `pyproject.toml`）
- Storage `copy()` 介面 T-018 已存在；若 thumbnail 抽幀失敗 fallback 用 transparent placeholder（避免整 task 失敗）
