# T-042: fix gpt-image API contract on real provider

**Status:** DONE
**Sprint:** 3
**Est:** XS
**Depends on:** none
**Related:** T-014（原始 client）、T-030（image2image / inpaint / multi-image edits extension）

---

## Scope

`api/app/ai/gpt_image_2.py` 對 OpenAI `gpt-image-*` API 有兩類 contract 假設錯誤，導致 `AI_STUB_MODE=false` 跑 real API 時失敗。兩類在同一次調查中發現，scope 合併處理。

### 類別 A：dall-e-3 遺留參數（5 個 method 全中）

User 看到「模型輸入不合法，請重新嘗試」（`MODEL_INVALID_REQUEST`）。Empirical probe（2026-05-01，gpt-image-2 + 本機 .env 的 OPENAI_API_KEY）：

- baseline `{model, prompt, size, n}` → **HTTP 200**
- 加 `response_format=b64_json` → **400** "Unknown parameter: 'response_format'."
- 加 `quality=hd` → **400**
- 加 `seed=12345` → **400**

對照 `openai/openai-python` SDK type stub：`response_format` GPT 不支援（dall-e-2/3 only）、`seed` schema 不存在、`quality=hd` 是 dall-e-3 legacy；GPT 只收 `low/medium/high/auto`。GPT 永遠回 `b64_json`，無 URL 形式。

### 類別 B：multi-image 多 part 的 multipart shape（`edit_image2image` 帶 reference 時）

T-030 的 code 註解原本寫「Repeated `image` field name ... matches OpenAI's gpt-image-1 multi-image edits contract; gpt-image-2 is assumed to inherit ... verify against the live provider before T-031 production cutover」——本 ticket 就是那個 verify 動作。

Empirical：

- `edit_image2image` 沒 ref（單一 `image` part） → **200**
- `edit_image2image` 帶 ref（多 `image` part 重複欄名） → **400** "Duplicate parameter: 'image'. ... use the array syntax instead e.g. 'image[]=<value>'."
- `edit_image2image` 帶 ref（field 改成 `image[]`） → **200**（gpt-image-1.5 + gpt-image-2 都驗過）

**In scope:**
- 類別 A：`gpt_image_2.py` 5 個 method 的 outgoing body 清乾淨（response_format × 5、quality=hd × 1、seed × 3）
- 類別 B：`edit_image2image` 在 `reference_image_bytes` 非空時把 multipart field 從 `image` 換成 `image[]`；單一 image 路徑保持 `image`
- `seed` 簽章在 Python client 保留（callers `create_checkpoint.py` / tests 仍依賴），但 outgoing body 不再夾帶；加註解說明 GPT 不吃
- `quality=hd` 直接刪掉（讓 OpenAI 用 `auto` 預設）；之後若要 explicit 高畫質再開 ticket 加 env-driven knob
- 既有 unit tests 跑綠就行；不額外加 real-API integration test（每次 CI 燒錢）

**Not in scope:**
- 重新引入 seed 的替代機制（gpt-image 沒有 seed；planning 若需要 deterministic 生成要另外設計，不在這張）
- 把 `quality` 變成 env-driven knob
- 校正 `_size_for` 內任何 1792×* 的舊 dall-e-3 尺寸（目前 mapping 都是合法 GPT size）
- Sync `planning/backend/ai-integration.md` §3 範例 body（仍寫舊 dall-e-3 shape）；reviewer 建議的 follow-up 留待後續 ticket，避免 scope 漂移

---

## Planning refs

- `planning/backend/ai-integration.md` §3 — gpt-image-2 image gen / edits / inpaint contract（注意：本 ticket 後此檔範例 body 仍是舊 shape，待 follow-up ticket sync）
- `api/app/ai/gpt_image_2.py` — 實際被改的檔案
- `openai/openai-python` `src/openai/types/image_generate_params.py` & `image_edit_params.py` — ground truth schema

---

## Acceptance criteria

- [x] `gpt_image_2.py` 5 個 method 的 outgoing body 都不再含 `response_format`、`seed`、`quality=hd`
- [x] `seed` 仍可從 caller 傳入（簽章不變），只是 silently 不送給 OpenAI；註解說明
- [x] `edit_image2image` 帶 reference 時用 `image[]`、不帶時保持 `image`
- [x] `docker compose exec api pytest tests/ai/test_gpt_image_2.py tests/ai/test_gpt_image_2_edit.py tests/checkpoints/` 全綠（21 pass / 23 skip）
- [x] 真機驗證 5 條 method 全部 200：
  - `generate_image_text2image`：1.4MB PNG, gpt-image-1.5
  - `generate_image_image2image`：193KB PNG, 28.2s
  - `generate_image_inpaint`：131KB PNG, 17s
  - `edit_image2image` 沒 ref：430KB PNG（沿用 baseline 路徑）
  - `edit_image2image` 帶 ref：gpt-image-1.5 214KB + gpt-image-2 840KB（兩個 SKU 都驗）
  - `edit_inpaint`：1.3MB PNG, 19s

---

## Files expected to touch

- `api/app/ai/gpt_image_2.py` (edit)

---

## Notes

- CI 沒擋是因為 `httpx.MockTransport` 只 assert HTTP method + URL，沒檢查 body schema / multipart field name。長期應該加一個 outgoing-body contract test 把實際送出的 body 跟 SDK type stub 對齊；本 ticket 不動，留 follow-up
- `response_format` 在 `reconciler_client.py:115` 是 chat completions API 的合法用法（`{"type": "json_object"}`），不要動
- 為什麼 default 不改成 `quality=auto` 而是直接砍：planning 沒指定 quality 偏好，少送一個欄位等於不主張，未來要做 quality knob 時再加最乾淨
- 為什麼 `image[]` 只用在帶 reference 的情境，不全面切換：empirical 已驗證單一 image 用 `image` 200、用 `image[]` 沒測——保守維持不破已驗證的快樂路徑
