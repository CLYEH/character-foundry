# T-035: Backend — Prompt preview extension for alias / motion modes

**Status:** TODO
**Sprint:** 3
**Est:** S (1h)
**Depends on:** T-019（既有 prompt-preview 端點，僅支援 `create_base`）
**Related:** T-040（frontend modal 對接 alias / motion mode）；T-031 / T-033（生成 worker 用同一份 reconciler）；同時收掉 STATUS.md 的 backlog **S3-1**（mask schema）與 **S2-5**（base_checkpoint_id 欄位）—— 本單把 schema 一次補齊

---

## Scope

把 `POST /v1/prompt/preview` 從只支援 `create_base` 擴到也支援 `create_alias` 與 `create_motion`，並把 `mask` 從 `dict[str, Any]` 換成嚴謹的 Pydantic schema（`MaskInput`），順手收掉 S3-1 / S2-5 backlog。

**In scope:**
- 擴 `api/app/schemas/prompt.py` 的 request schema：
  - `mode: Literal['create_base', 'create_alias', 'create_motion']`
  - 新欄位（per mode 必選）：
    - `create_alias`：`character_id` (UUID) — backend 讀 base 的 prompt_summary 與 image hint；`input_mode: 'text' | 'image' | 'inpaint' | 'mixed'`
    - `create_motion`：`parent_type: 'base' | 'alias'`、`parent_id: UUID`、`motion_type: 'preset_*' | 'custom'`、`description: str | null`
  - **`mask` 換成 `MaskInput` Pydantic model**：`{ mask_id: UUID }`（對齊 T-031 上傳 + 帶 id 的設計）
    - 空物件 `{}` → 422 `VALIDATION_MASK_REQUIRED`（修掉 S3-1）
  - `base_checkpoint_id: UUID | None` — `create_base` mode 的 remix 場景用，給 `/prompt/preview` 也能 faithful 呈現 image2image + has_reference_image=True 的 prompt（修掉 S2-5）
- Extend `prompt_service.preview()`：
  - `create_alias` mode：reconciler 走 `create_alias` 模式（base 已合規，補述只描述差異），組出最終英文 prompt + reference / mask hint summary
  - `create_motion` mode：preset 直接讀 yaml prompt（T-033 同份）；custom 走 reconciler `create_motion` 模式
- Response schema 新增欄位（依 mode 才出現）：
  - `create_alias`：`derived_from: { base_id, base_image_url }`
  - `create_motion`：`parent: { type, id, image_url }`、`motion_template_used: 'preset_*' | 'custom_reconciled'`
- 不執行生成，只回 prompt 結構
- 測試：三種 mode、empty mask 422、preset_* 不過 reconciler

**Not in scope:**
- 任何實際的 image / video 生成
- Frontend 對接（T-040）
- 真實的 reconciler `create_alias` / `create_motion` 模式定義（以 T-015 reconciler 模組為主，本單只是呼叫）—— 若 reconciler 缺對應 mode，補上一個極簡實作（identity passthrough + 加 constraint suffix），讓 worker 能跑

---

## Planning refs

- `planning/backend/api-shape.md` §5.6 Prompt Preview
- `planning/backend/prompt-reconciler.md` §modes
- STATUS.md backlog S3-1（mask schema）、S2-5（base_checkpoint_id）
- T-019 既有 endpoint 為 baseline

---

## Acceptance criteria

- [ ] `mode='create_alias'` happy → 回 final_prompt + derived_from
- [ ] `mode='create_motion'` preset → 回 preset prompt（不過 reconciler）+ `motion_template_used='preset_*'`
- [ ] `mode='create_motion'` custom → 過 reconciler，回英文 prompt
- [ ] `mask: {}` → 422 `VALIDATION_MASK_REQUIRED`
- [ ] `mask: { mask_id }` mask 不存在 → 404 `NOT_FOUND_MASK`
- [ ] `create_base` 加 `base_checkpoint_id` → reconciled prompt 帶「has_reference_image=True」訊號
- [ ] 缺 mode-specific 必填欄位 → 422
- [ ] Non-owner 嘗試讀別 character 的 prompt → 403
- [ ] OpenAPI 正確產出（mode-specific 欄位走 discriminated union）
- [ ] `pytest api/tests/prompt/test_preview.py` 全綠

---

## Files expected to touch

- `api/app/api/routes/prompt.py` (edit)
- `api/app/services/prompt_service.py`（or 對應，edit）
- `api/app/schemas/prompt.py` (edit) — discriminated union by `mode`
- `api/app/repositories/`（小編輯 — 取得 alias / motion parent 用）
- `api/app/prompt/reconciler_modes.py`（or 對應，edit） — 補 `create_alias` / `create_motion` minimal mode
- `api/tests/prompt/test_preview.py` (edit)

---

## Notes

- 用 Pydantic `Field(discriminator='mode')` 讓 OpenAPI 出來乾淨；frontend 也能照 generated types 寫 union narrow
- `MaskInput` 之後若要支援多種 mask 來源（polygon、bbox），擴 union 即可——本單先只接 `{ mask_id }`
- 收 S3-1 / S2-5 backlog 後同步把 STATUS.md 那兩列移除（在本單 PR 內順手做）
- 不在本單動 reconciler 的真正 prompt 模板細節（語意 by 模型 owner）；只要 endpoint 不會 500、回的 prompt 夠 frontend 顯示 placeholder 即可
