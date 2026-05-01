# T-042: fix gpt-image API contract — drop response_format / seed / quality=hd

**Status:** TODO
**Sprint:** 3
**Est:** XS
**Depends on:** none
**Related:** T-014（原始 client）、T-030（image2image / inpaint extension）

---

## Scope

`api/app/ai/gpt_image_2.py` 在 5 處 outgoing request body 塞了 OpenAI 現行 `gpt-image-*` API 不收的欄位，導致 `AI_STUB_MODE=false` 跑 real API 時 100% 拿到 `MODEL_INVALID_REQUEST`（user 看到「模型輸入不合法，請重新嘗試」）。

Empirical probe（2026-05-01，gpt-image-2，本機 .env 的 OPENAI_API_KEY）：
- baseline `{model, prompt, size, n}` → **HTTP 200**
- 加 `response_format=b64_json` → **400** "Unknown parameter: 'response_format'."
- 加 `quality=hd` → **400**
- 加 `seed=12345` → **400**

對照 `openai/openai-python` SDK type stub（main 分支）：`response_format` GPT 不支援、`seed` schema 不存在、`quality=hd` 是 dall-e-3 legacy；GPT 系列只接受 `low / medium / high / auto`。回應 shape GPT 永遠 `b64_json`，`response_format` 對 GPT 不必要也不合法。

**In scope:**
- `gpt_image_2.py` 5 個 method 的 outgoing body 清乾淨（generations 一處 + edits 四處）
- `seed` 簽章在 Python client 保留（callers `create_checkpoint.py` / tests 仍依賴），但 outgoing body 不再夾帶；加註解說明 GPT 不吃
- `quality=hd` 直接刪掉（讓 OpenAI 用 `auto` 預設）；之後若要 explicit 高畫質再開 ticket 加 env-driven knob
- 測試：既有 unit tests 不 assert 這些欄位，跑過保綠就行；不額外加 real-API integration test（會燒錢）

**Not in scope:**
- 重新引入 seed 的替代機制（gpt-image 沒有 seed；planning 若需要 deterministic 生成要另外設計，不在這張）
- 把 `quality` 變成 env-driven knob
- 校正 `_size_for` 內任何 1792×* 的舊 dall-e-3 尺寸（已確認目前 mapping 都是合法 GPT size）

---

## Planning refs

- `planning/backend/ai-integration.md` §3 — gpt-image-2 image gen / edits / inpaint contract
- `api/app/ai/gpt_image_2.py` — 實際被改的檔案
- `openai/openai-python` `src/openai/types/image_generate_params.py` & `image_edit_params.py` — ground truth schema

---

## Acceptance criteria

- [ ] `gpt_image_2.py` 5 個 method 的 outgoing body 都不再含 `response_format`、`seed`、`quality=hd`
- [ ] `seed` 仍可從 caller 傳入（簽章不變），只是 silently 不送給 OpenAI；註解說明
- [ ] `docker compose exec api pytest tests/ai/test_gpt_image_2.py tests/ai/test_gpt_image_2_edit.py` 全綠
- [ ] `docker compose exec api pytest tests/checkpoints/` 全綠（worker 端不該破）
- [ ] 真機驗證：`AI_STUB_MODE=false` 在本機觸發一次 checkpoint 生成，DB `tasks.status='completed'`、`generation_logs` 寫入 success 列、UI 看得到圖

---

## Files expected to touch

- `api/app/ai/gpt_image_2.py` (edit)

---

## Notes

- CI 沒擋是因為 `httpx.MockTransport` 只 assert HTTP method + URL，沒檢查 body schema。長期應該加一個 lint / contract test 把 outgoing body 跟 SDK type stub 對齊；先不在這張動，避免 scope 蔓延
- `response_format` 在 `reconciler_client.py:115` 是 chat completions API 的合法用法（`{"type": "json_object"}`），不要動
- 為什麼 default 不改成 `quality=auto` 而是直接砍：planning 沒指定 quality 偏好，少送一個欄位等於不主張，未來要做 quality knob 時再加最乾淨
