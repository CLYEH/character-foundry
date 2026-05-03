# T-044: outgoing-body contract test for gpt-image client

**Status:** TODO
**Sprint:** 3
**Est:** S
**Depends on:** none
**Related:** T-042（這張就是 T-042 ship 後 reviewer 提的 follow-up）

---

## Scope

T-042 修的兩類 bug（dall-e-3 遺留 params + multi-image 欄名）都溜過 CI，因為現有 `tests/ai/test_gpt_image_2*.py` 用 `httpx.MockTransport` 只 assert HTTP method + URL，不檢查 body schema。下次 OpenAI 改 contract 或 code 不小心又寫錯 param 又會 production 才炸。

加一組 contract test 把 `GptImage2Client` 5 個 method 的 outgoing body / multipart shape 跟 OpenAI 公開 schema 對齊，CI 跑。

**In scope:**
- 新增 `api/tests/ai/test_gpt_image_2_contract.py`
- 對 5 個 method 各跑一次正常輸入，捕 outgoing request 並 assert：
  - `/images/generations` JSON body 只含 `{model, prompt, size, n}` 這個 baseline 集合（外加 `output_format` / `quality` 等可選 GPT 合法 param）
  - `/images/edits` multipart：單圖路徑用 `image` field name；多圖路徑用 `image[]`，數量 = base + ref 數
  - 任何 method 都**不可**送 `response_format` / `seed` / `quality=hd`（regression guard）
- Schema 來源：`openai/openai-python` 的 `image_generate_params.py` / `image_edit_params.py`（手動 mirror enum 集合即可，不需要 runtime import；SDK 是 dev tool，prod 不依賴它）

**Not in scope:**
- Real-API integration test（會在每次 CI 燒 credit）
- 同 contract test 套用到 Veo / reconciler client（若要也另開單，或一起做）
- 換掉 `httpx.MockTransport` 機制本身

---

## Planning refs

- `planning/backend/ai-integration.md` §3 — gpt-image API contract（T-043 sync 後是 ground truth）
- `api/app/ai/gpt_image_2.py` — 受測物
- `openai/openai-python` `src/openai/types/image_generate_params.py` & `image_edit_params.py` — schema 來源

---

## Acceptance criteria

- [ ] `tests/ai/test_gpt_image_2_contract.py` 新增；至少 5 個 test cases（各 method 一個）
- [ ] Test 失敗時錯誤訊息明確指出哪個欄位多 / 少 / 錯
- [ ] Test 執行時間 < 1s（純 in-memory，無真打）
- [ ] 把 T-042 移除過的三個 param 都納入 regression guard：response_format、quality=hd、seed
- [ ] CI 綠

---

## Files expected to touch

- `api/tests/ai/test_gpt_image_2_contract.py` (new)
- 可能微調 `tests/ai/conftest.py` 共用 fixture

---

## Notes

- Contract test 的精神：**code 寫的 outgoing body** 必須是 **provider 接受的 outgoing body 子集**；多送會 400、少送會 caller 行為改變
- 為什麼不直接 import `openai-python` SDK 的 types：那是個 dev SDK，會帶大量間接依賴；我們只需要它的常數 / enum，手動 mirror 一份比較乾淨。Mirror 過時的風險靠 CI 真打 smoke 測試補（不在這張 scope）
- 若日後加 Veo / reconciler client 的 contract test，可以共用 fixture / helper，但本張只動 gpt-image
