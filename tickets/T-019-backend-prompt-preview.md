# T-019: Backend — Prompt preview endpoint

**Status:** TODO
**Sprint:** 2
**Est:** XS (30m)
**Depends on:** T-015 (reconciler)
**Related:** T-024（frontend M-01 modal）

---

## Scope

薄的 endpoint 包住 reconciler：不啟任何 task、不寫 DB、只組 prompt 回傳。給 UI「進階檢視」用。

**In scope:**
- `POST /v1/prompt/preview` — body：
  ```
  {
    mode: 'create_base' | 'create_alias' | 'create_motion',
    menu_selections: dict | null,
    freeform_note: str | null,
    reference_image_ids: [UUID] | null,
    mask: {...} | null      # alias 用，schema 對齊 T-015 reconciler input；本單先允許但忽略
  }
  ```
- 回：
  ```
  {
    platform_constraints: "...",
    reconciled_note_en: "...",
    menu_fragments: ["...", ...],
    final_prompt: "..."
  }
  ```
- Validation：mode 必填；至少一項輸入（menu / note / reference / mask）否則 400
- 呼 `PromptReconciler.reconcile(...)`（含 Redis cache，不會每次打 LLM）
- 單元 + integration test

**Not in scope:**
- 生成圖片
- Motion prompt preview（Sprint 3 若需要再加）

---

## Planning refs

- `planning/backend/api-shape.md` §5.6 — preview endpoint spec
- `planning/product/functional-scope.md` §4.1 F-04b — prompt 透明度規範

---

## Acceptance criteria

- [ ] `POST /v1/prompt/preview` 回 4 個欄位，`final_prompt` 非空字串
- [ ] 空 input → 400 `VALIDATION_EMPTY_INPUT`
- [ ] Reconciler 失敗 → 400 `PROMPT_CONFLICT` 含 AgentError
- [ ] 相同 input 連打兩次，第二次從 cache 回（log 可觀測）
- [ ] OpenAPI 正確產出
- [ ] `pytest api/tests/prompt_preview/` 全綠

---

## Files expected to touch

- `api/app/routers/prompt.py` (new)
- `api/app/schemas/prompt.py` (new)
- `api/app/main.py` (edit)
- `api/tests/prompt_preview/` (new)

---

## Notes

- Endpoint 不需要 task；純同步
- 不在回應裡洩漏 LLM raw response（只給 reconciler 包好後的欄位）
- Integration test 用 stub reconciler client；避免打外部 API
