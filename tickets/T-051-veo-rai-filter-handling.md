# T-051: 偵測 Veo 3.1 RAI filter，修正誤導性的 MODEL_INVALID_REQUEST

**Status:** TODO
**Sprint:** 3
**Est:** S
**Depends on:** T-029（Veo client）
**Related:** T-033（motion worker）、T-042 / T-045（同類「provider contract drift」fix 家族）

---

## Scope

User 真機 base→motion 觸發 task `371fc9a8-bd62-49de-b778-177040fbe8b2` 失敗。回應 envelope：

```json
{
  "code": "MODEL_INVALID_REQUEST",
  "message": "模型輸入不合法，請重新嘗試",
  "problem": "veo-3.1-generate-preview returned 4xx for the request payload. Detail: operation.response did not include any video items",
  "cause": "Client-side payload mismatched the provider's schema (unsupported size, malformed image, etc).",
  "fix": "Inspect the request payload; this is a bug if the input looked valid.",
  "retryable": false
}
```

實況**不是 4xx**——Veo long-running operation 順利 `done: true`，但 RAI（Responsible AI）filter 把生成的影片過濾掉了。回應 shape：

```json
{
  "done": true,
  "response": {
    "generateVideoResponse": {
      "raiMediaFilteredCount": 1,
      "raiMediaFilteredReasons": ["We encountered an issue with the audio for your prompt..."]
    }
  }
}
```

`_extract_videos`（`api/app/ai/veo_3_1.py:526-568`）只認 `videos[]` 與 `generatedSamples[]` 兩種 shape，回傳 `[]`；`_fetch_video_bytes:319-332` 接著丟 `model_invalid_request(detail="operation.response did not include any video items")`。

而 `model_invalid_request`（`api/app/ai/errors.py:154-167`）的 `problem` template 又無條件 prepend `"{model} returned 4xx for the request payload."`——所以最終訊息變成「returned 4xx」，但實際上沒有任何 4xx 發生。

Google 自己已知 Veo 3.1 RAI 有 false positive 多的問題（`googleapis/js-genai#1272`）：同樣 prompt+image 重試 3-5 次通常會有一次成功。直接當「使用者輸入不合法」並 non-retryable 是錯的分類。

**In scope:**

1. **偵測 RAI filter case**：明確放在 `_poll_until_done`（`api/app/ai/veo_3_1.py:283-317`）裡 `done: true` 那刻，跟現行 line 305-309 的 `payload.get("error")` 檢查並列——也就是說「terminal envelope 的詮釋」集中在一處，`_fetch_video_bytes` 只負責「我已知有 videos，把 bytes 取出來」。檢查條件：`response.generateVideoResponse.raiMediaFilteredCount > 0` 或 `raiMediaFilteredReasons` 非空。命中就丟新的 `MODEL_CONTENT_FILTERED`。
2. **新 error factory `model_content_filtered`**：`retryable=True`，承認上游 known flake；user-facing `message` 走「目前無法生成此動作影片，系統正在自動重試」之類措辭；`fix` 給內部 hint「Veo RAI false positive 已知；超過 N 次後請使用者改 prompt」。
3. **`generate_i2v` 加 post-submit RAI retry 預算**：現行 retry envelope 只覆蓋 submit 步（per planning §4.4「影片重試很貴」設計），但 RAI 過濾發生在 submit 之後 operation 完成那刻。本單需引入 **小幅** post-RAI-filter retry budget（例如 1-2 次，env-tunable `VEO_RAI_MAX_RETRIES`），耗盡後才 surface 給 user。retry 走完整 submit path（這是 Veo 已知的 flake，重新 submit 才有意義；單純重 poll 沒用）。
4. **修 `model_invalid_request` template**：把 hardcoded `"{model} returned 4xx for the request payload."` 改成只在真的 HTTP 4xx 路徑才這樣寫。`detail`-only 路徑（schema 不符、redirect chain 超限）改成 `f"{model} returned an unexpected response. Detail: {detail}"` 或新增獨立 factory。Audit 範圍：repo-wide `grep -rn "model_invalid_request(" api/`，逐個 caller 確認語意對得上（不限 `veo_3_1.py` 5 個點，避免遺漏其他 client / 未來 reconciler / gpt-image-2 的呼叫端）。
5. **單元測試**：
   - `test_veo_3_1.py` 加 fixture 覆蓋 `done: true` + `raiMediaFilteredCount: 1` shape，斷言丟 `MODEL_CONTENT_FILTERED`、retry envelope 在 budget 內重 submit、超過 budget 才 surface。
   - `test_errors.py`（若有）或 `test_veo_3_1.py` 補一個 case 確認 `model_invalid_request` 不再對非 4xx 路徑說 4xx。

**Not in scope:**

- 把 RAI 過濾原因（`raiMediaFilteredReasons` 字串）回傳給前端使用者：planning 沒這需求且字串對普通 user 沒意義；只記到 `GenerationLog.raw_response` 給 ops 用。
- 改 `i2v` retry policy 的整體哲學（submit-only 不變，本單只多開一條「post-RAI 小預算 retry」例外）。
- gpt-image-2 路徑的對應檢查（不同 provider 行為，沒看到類似回報）。

---

## Planning refs

- `planning/backend/ai-integration.md` §4.2 / §4.4 — Veo i2v 流程 + 「影片重試很貴」原則（本單會在這條原則上開小例外，要在 §4.4 加段落說明 RAI 例外）
- `tickets/DONE/T-029-backend-veo-i2v-client.md` — Veo client 既有 retry envelope 設計
- `api/app/ai/veo_3_1.py:319-332`（`_fetch_video_bytes`）+ `:526-568`（`_extract_videos`）—— 修改點
- `api/app/ai/errors.py:154-167`（`model_invalid_request`）—— template 修改點
- `googleapis/js-genai#1272` — Google 認帳 Veo 3.1 RAI false positive 多
- `tickets/DONE/T-042-fix-gpt-image-api-contract.md` / `T-045-fix-reconciler-max-completion-tokens.md` — 同類「provider 行為跟代碼預設不符」fix pattern 範例

---

## Acceptance criteria

- [ ] `_extract_videos` 之前先偵測 `raiMediaFilteredCount > 0` / `raiMediaFilteredReasons` 非空，走新分支
- [ ] 新 factory `model_content_filtered` 加進 `errors.py`，`code=MODEL_CONTENT_FILTERED`、`retryable=True`、`status_code=502`、user-facing 中文訊息溫和
- [ ] `generate_i2v` 內 post-RAI retry budget（env: `VEO_RAI_MAX_RETRIES`，default 2），耗盡後 surface 給 user
- [ ] **每次 RAI retry 都寫 log 行 / metric**：`generation_log` 多一筆（attempt 編號 + raiMediaFilteredReasons 全文）或結構化 logger 一筆，確保 ops 看得到 false-positive rate 才能調 `VEO_RAI_MAX_RETRIES`（沒這條會 blind-tune）
- [ ] `model_invalid_request` template 不再對非 4xx 路徑硬塞「returned 4xx」字串；audit 走 `grep -rn "model_invalid_request(" api/` 涵蓋全 repo，逐 caller 確認
- [ ] `pytest tests/ai/test_veo_3_1.py` 全綠（含新 RAI fixture 與 retry 行為斷言）
- [ ] `planning/backend/ai-integration.md` §4.4 加段落說明 RAI 例外
- [ ] DECISIONS.md 視情況補一條「Veo RAI false positive → silent retry」決策（若已有 retry 哲學在那兒）

---

## Files expected to touch

- `api/app/ai/veo_3_1.py` (edit) — RAI 偵測 + post-submit retry 例外
- `api/app/ai/errors.py` (edit) — 新 `model_content_filtered` factory + 修 `model_invalid_request` template
- `api/app/ai/config.py` (edit) — 加 `veo_rai_max_retries()`
- `api/tests/ai/test_veo_3_1.py` (edit) — 新增 RAI shape fixture + retry 斷言
- `planning/backend/ai-integration.md` (edit) — §4.4 補 RAI 例外段落
- `planning/devops/environment-variables.md` (edit) — 補 `VEO_RAI_MAX_RETRIES`
- `tickets/T-051-veo-rai-filter-handling.md` (new — 本單)
- `STATUS.md` (edit) — 加 T-051 row

---

## Notes

- **為什麼用 retryable=True 而不是 PROMPT_CONTENT_POLICY**：Google 自己承認 Veo 3.1 RAI 是 flaky，false positive 多到 same prompt+image 重試常常就過。叫 user 改 prompt 不對症，且現行 `prompt_content_policy` 是 non-retryable。把 RAI 過濾跟「user 寫了違規 prompt 被 OpenAI 擋」分開，比較貼合上游現實。
- **Retry 設計 trade-off**：submit-only retry 是現行原則（T-029），因為 Veo submit 失敗代表還沒開算錢。RAI 過濾是「已經算完但被擋」，重試代表**再付一次錢**。所以 budget 要小（2 次足以吃掉 90% false positive，再多就該讓 user 改 prompt）。env-tunable 讓 ops 在 incident 時可以暫時調 0 stop bleeding。
- **`model_invalid_request` template 修法**：最小變動是把「returned 4xx」prefix 移到 `map_response_to_agent_error` 那條真正 HTTP 4xx 的呼叫端，detail-only 路徑換句話。Audit 5 個現有 detail 路徑時注意：submit response missing `name`、poll response 不是 dict、video item 缺 bytes/uri、redirect 缺 Location、redirect chain 超限——這 5 個都不是 4xx，本單通通要重新措辭。
- **不要把 RAI reason 字串回前端**：那串文字是 Google 給 developer 看的，不是給 end user。記到 `generation_log.raw_response` 即可，前端走通用「無法生成，請稍後再試或更改描述」訊息。
- **長尾**：Veo 也可能有非 RAI 原因的「`done: true` + 空 videos」（quota / region / 模型未知狀態），但目前沒看到實例。本單先處理已驗證的 RAI 路徑；其他空-videos 變體仍走 `_fetch_video_bytes` 的兜底（修過 template 後的訊息會是「unexpected response」，不再是 misleading 的「4xx」）。
- **`MODEL_CONTENT_FILTERED` status_code 取捨**：現行同類 factory（`model_invalid_request`、`model_quota_exceeded`、`model_unavailable`）大多用 502，但 RAI 過濾在語意上不太是「上游壞了」。實作時評估 422（unprocessable entity，傳得到但結果不可用）vs 502（沿用 provider-issue 慣例）。沒有對錯，pick one 並在 PR 說明選擇理由即可。
