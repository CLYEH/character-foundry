# T-015: Backend — Prompt Reconciler module (gpt-5-mini)

**Status:** TODO
**Sprint:** 2
**Est:** S (1h)
**Depends on:** T-014 (AI client infra、circuit breaker)
**Related:** T-017（checkpoint worker 呼叫）、T-019（prompt preview endpoint）

---

## Scope

Pure Python 模組：吃使用者輸入（中文選單選項 + 自由補述）與平台固定 constraints，呼 gpt-5-mini 翻譯 + 衝突解決 + 組合，吐最終英文 prompt。獨立可測試，無 HTTP / DB 依賴。

**In scope:**
- 兩個方法（對齊 `planning/backend/prompt-reconciler.md` line 62–63, 240）：
  - `PromptReconciler.reconcile(input) -> ReconcilerOutput` — cache miss 時寫 cache（供 worker 生成使用）
  - `PromptReconciler.preview(input) -> ReconcilerOutput` — 同邏輯但**不寫 cache、不 log**（供 T-019 的「進階檢視」用，避免 preview 污染正式 cache）
  - Input：`menu_selections` dict、`freeform_note` 中文、`mode` (create_base / create_alias / create_motion)、`reference_image_count`
  - Output：`platform_constraints`、`reconciled_note_en`、`menu_fragments[]`、`final_prompt`
- 平台固定 constraints（見 `functional-scope.md` §7）寫成 const 字串：transparent bg / centered / facing camera / full body / consistent lighting
- Menu 選項 → English fragment mapping 表（性別 / 眼型 / 鼻型 / 髮型 / 膚色 / 體型 / 風格；每項若干 option）
- gpt-5-mini prompt template：給模型吃中文補述 + 當前 constraints + 指定 JSON 輸出
- JSON 輸出 validate（失敗時 raise `PROMPT_CONFLICT` with fix）
- Redis cache：`(hash(input), constraints_version)` → output，**TTL 24h**（對齊 `planning/backend/prompt-reconciler.md` line 236）
- 單元測試：衝突 case（「雜亂市場背景」）、純中翻英 case、menu-only case（無補述）、cache hit、LLM fail fallback（回結構化錯誤，不把中文當英文直送 gpt-image-2）

**Not in scope:**
- HTTP endpoint（T-019 包起來）
- Veo 3.1 的 motion prompt（Sprint 3 用另一組 template）
- `reference_image_ids` → actual image content analysis（Phase 1 reconciler 不看圖）

---

## Planning refs

- `planning/backend/prompt-reconciler.md` — 完整設計（prompt template、sanitization、cache）
- `planning/product/functional-scope.md` §4.1 F-04a/b、§7（fixed constraints）、§8（language strategy）
- `planning/backend/api-shape.md` §4.1 `PROMPT_CONFLICT`、§5.6 preview endpoint output shape

---

## Acceptance criteria

- [ ] `reconcile(menu={...}, note="雜亂市場背景的古風美女", mode="create_base")` → `reconciled_note_en` **移除** "cluttered market" 類詞、保留「古風美女」相關
- [ ] 相同 input 第二次呼叫走 cache（LLM 不再被呼叫）
- [ ] `final_prompt` 結構：`<constraints>, <menu_fragments joined>, <reconciled_note_en>`
- [ ] LLM 回非法 JSON → raise `PromptReconcilerError` with `code=PROMPT_CONFLICT`，worker 把它包成 task.error 後 task fail
- [ ] `constraints_version` bump 後 cache 自動失效
- [ ] `pytest api/tests/prompt_reconciler/` 全綠（含 stub LLM fixture）

---

## Files expected to touch

- `api/app/prompt/reconciler.py` (new)
- `api/app/prompt/constraints.py` (new) — fixed constraints const
- `api/app/prompt/menu_fragments.py` (new) — mapping 表
- `api/app/prompt/errors.py` (new) — `PromptReconcilerError`
- `api/app/ai/reconciler_client.py` (new) — wraps gpt-5-mini via AIClient pattern
- `api/tests/prompt_reconciler/` (new) — 單元 + snapshot tests
- `planning/backend/prompt-reconciler.md` (edit 若需對齊)

---

## Notes

- gpt-5-mini 用 **OpenAI responses API + JSON mode**，temperature=0 提升穩定性
- Cache key 用 SHA256 of `(sorted(menu_selections), note, mode, constraints_version, model_version)`
- Menu fragments 的 mapping 表先放最小可行子集（每項 3-5 個 option），剩下 UX 實作時補（見 STATUS.md backlog M5）
- `constraints_version` 是純字串版本號（例：`v1`），改 constraints 時手動 bump 並寫進 `/v1/meta`
- Reconciler 不應該 self-reference task system；純 lib，T-017 的 worker 來 wrap async + 錯誤轉成 AgentError
- 單元測試用 fixture LLM client 避免打外部 API（`AI_STUB_MODE` 會把 reconciler 接口也切到 stub，回 hand-crafted 英文）
