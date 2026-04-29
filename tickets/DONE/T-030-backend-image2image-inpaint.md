# T-030: Backend — gpt-image-2 image2image + inpaint extension

**Status:** TODO
**Sprint:** 3
**Est:** M (2h)
**Depends on:** T-014（既有 gpt-image-2 client 只實作 text2image）
**Related:** T-031（alias generation 會呼本單擴充的方法）；T-035（prompt-preview alias mode 也走同份組合邏輯）

---

## Scope

擴充 `api/app/ai/gpt_image_2.py` 的能力面，從只能 text-to-image 變成也能 image-to-image 與 inpaint。Sprint 2 Base 生成不需要這兩種；Sprint 3 Alias 需要。**只動 client 層**——routing / 業務邏輯由 T-031 處理。

**In scope:**
- `class GptImage2Client` 加兩個 method：
  - `async def edit_image2image(self, *, base_image_bytes: bytes, reference_image_bytes: list[bytes] | None, prompt: str) -> ImageResult`
  - `async def edit_inpaint(self, *, base_image_bytes: bytes, mask_png_bytes: bytes, prompt: str) -> ImageResult`
- Mask 規格：PNG bitmap，alpha channel 為 mask（per UX §6 row 3、`api-shape.md` §5.3）
  - Client 驗證 mask 尺寸 = base 圖尺寸；不符 → 拋 `VALIDATION_MASK_SIZE_MISMATCH`（**新 error code**，加進 `api/app/ai/errors.py` enum）
  - Mask 全黑（無覆蓋）→ 拋 `VALIDATION_MASK_EMPTY`
- 共用 timeout / retry / circuit breaker（既有 `circuit.py`，key 仍是 `gpt-image-2`）
- Stub 模式（`stub.py` `GptImage2Stub`）：兩個新 method 回 `_fixtures/` 對應的不同 sample png（image2image 用 `edit_sample.png`、inpaint 用 `inpaint_sample.png`）
- 測試：
  - happy：image2image 三張 reference + prompt → 回 bytes
  - happy：inpaint 給對的 mask 尺寸 → 回 bytes
  - error：mask 尺寸錯 → `VALIDATION_MASK_SIZE_MISMATCH`
  - error：mask 全黑 → `VALIDATION_MASK_EMPTY`
  - circuit breaker 行為對齊既有 text2image 測試

**Not in scope:**
- Alias 業務邏輯（T-031）
- Mask 前處理（resize / threshold）—— Phase 1 frontend 已保證尺寸對齊（react-konva 鎖到 base 尺寸），backend 只驗
- `MaskInput` Pydantic schema（T-035 加，本單 client 介面接 `bytes`）

---

## Planning refs

- `planning/backend/ai-integration.md` §gpt-image-2
- `planning/ux/user-flows.md` §6 row 3 — mask PNG bitmap 約定
- `planning/backend/api-shape.md` §5.3 — alias body 接 `mask`
- T-014 的 `gpt_image_2.py` text2image 路徑與 retry 行為

---

## Acceptance criteria

- [ ] `edit_image2image()` 在 stub 模式回 fixture bytes
- [ ] `edit_inpaint()` 在 stub 模式回 fixture bytes，且驗證 mask 尺寸 = base 尺寸
- [ ] Mask 尺寸不符 → `VALIDATION_MASK_SIZE_MISMATCH`
- [ ] Mask 全黑 → `VALIDATION_MASK_EMPTY`
- [ ] HTTP 模式 timeout / 5xx 觸發 retry & circuit breaker（沿用 text2image 測試骨架擴充）
- [ ] `pytest api/tests/ai/test_gpt_image_2_edit.py` 全綠

---

## Files expected to touch

- `api/app/ai/gpt_image_2.py` (edit)
- `api/app/ai/stub.py` (edit)
- `api/app/ai/errors.py` (edit) — 加兩個新 error code
- `api/app/ai/_fixtures/edit_sample.png` (new)
- `api/app/ai/_fixtures/inpaint_sample.png` (new)
- `api/tests/ai/test_gpt_image_2_edit.py` (new)

---

## Notes

- gpt-image-2 真實 API 對 inpaint mask 的格式是 alpha-mask PNG（與 OpenAI image edit endpoint 一致），所以 client 直接把 bytes pass-through，不要做轉換
- Reference images（image2image 模式）Phase 1 上限同既有 reference-image upload（5 張，per T-016 / T-022）
- Reference image 對 prompt 的影響由 model 決定，client 不做 weighting；prompt 是組好的英文（reconciler 已處理）
- 不要在本單做 storage 讀檔——bytes 由 worker（T-031）從 storage backend 拉出來再傳進來
