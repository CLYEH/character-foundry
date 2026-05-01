# T-045: fix reconciler client for gpt-5-mini contract drift

**Status:** DONE
**Sprint:** 3
**Est:** XS
**Depends on:** none
**Related:** T-015（reconciler 原始 client）、T-042（同類「OpenAI API contract drift」fix）

---

## Scope

`api/app/ai/reconciler_client.py:114` 的 chat completions request body 在 OpenAI reasoning-style 模型（`gpt-5*` / `o1` / `o3` 系列）下 100% 拿 400。Empirical probe（2026-05-01，gpt-5-mini，本機 .env）驗到兩個 contract drift：

1. **`max_tokens` → `max_completion_tokens`**：
   > `Unsupported parameter: 'max_tokens' is not supported with this model. Use 'max_completion_tokens' instead.`
2. **`temperature` 只接受 default (1)**：
   > `Unsupported value: 'temperature' does not support 0 with this model. Only the default (1) value is supported.`

T-042 修完 gpt-image-2 client 之後，user 從前端跑生圖一進到 reconciler 這個 hop 就被卡住，UI 仍然顯示「模型輸入不合法，請重新嘗試」（已透過 chrome-devtools 重現）。同類「code 沒跟 OpenAI 新模型 contract 同步」的 bug 家族。

**In scope:**
- `reconciler_client.py:114` 換成 `max_completion_tokens`
- 移除 `"temperature": 0`（reasoning model 自帶 internal reasoning，溫度沒有 deterministic 意義；JSON-mode 由 `response_format={"type":"json_object"}` 強制，dropping temperature 不影響 structured-output 品質）
- 維持 Python 內部命名（`self.max_tokens`、`config.reconciler_max_tokens()`、env var `RECONCILER_MAX_TOKENS`）不變——它們是內部契約，operator 已經習慣 `RECONCILER_MAX_TOKENS`，重新命名會造成 .env / docker config drift
- 加註解說明 wire-level 改動 vs Python 內部命名為什麼不一致

**Not in scope:**
- 重新命名 env var `RECONCILER_MAX_TOKENS`（operator-facing breaking change）
- 加 model-conditional 邏輯（"非 reasoning model 還是送 max_tokens / temperature=0"）——專案 pin 在 gpt-5-mini，沒有降級需求；要支援多家模型 SKU 是另一條 ticket
- 改 `errors.py:121` 的中文錯誤訊息（仍引用 `max_tokens`，但對 operator 來說那個字串對應的是 env var 而非 wire 名稱）

---

## Planning refs

- `planning/backend/prompt-reconciler.md` — reconciler 設計（注意：本 ticket 後 §4 範例 body 也要 sync，不在這張，併入 T-043）
- `tickets/DONE/T-042-fix-gpt-image-api-contract.md` — 同類 fix 範例
- OpenAI Responses API / Chat Completions docs — `max_completion_tokens` 是 reasoning model 的契約

---

## Acceptance criteria

- [x] `reconciler_client.py` 改成 `max_completion_tokens` + 移除 `temperature: 0`
- [x] `docker compose exec api pytest tests/ai/test_reconciler_client.py tests/checkpoints/` 全綠（9 pass / 23 skip）
- [x] 真機 reconciler smoke：直接呼叫 `Gpt5MiniClient.call(...)` → 拿到合法 `final_prompt`
- [x] 真機端到端：從 chrome-devtools UI 登入 test+alice → 建 character `test-T-045` → 生 checkpoint → UI 顯示 `已完成` + `選作 Base` 按鈕（reconciler hop ✅、image gen ✅、storage ✅、DB ✅、SSE ✅）
- [ ] T-043（planning sync）的 backlog 單裡記下 reconciler 範例 body 也要 sync（最小掛勾，避免 T-045 自己 scope 漂移）

---

## Files expected to touch

- `api/app/ai/reconciler_client.py` (edit)

---

## Notes

- 為什麼不重新命名 `self.max_tokens`：用法上它仍然代表「模型輸出 token 上限」，OpenAI 改名只在 wire 層面。Python 內部用什麼名字無所謂；operator 看到的 `RECONCILER_MAX_TOKENS` 本質還是 max_tokens 概念
- `errors.py:121` 中文訊息「請縮短輸入或調高 max_tokens」對 operator 是 hint，看到去調 `RECONCILER_MAX_TOKENS`；technical accuracy 上若改成 max_completion_tokens 反而對 operator 不利（他要去找 env var 卻找 max_completion_tokens）
- T-044（contract test）也要納這個 case：reconciler 的 outgoing body 不可含 `max_tokens` 鍵，只能含 `max_completion_tokens`
