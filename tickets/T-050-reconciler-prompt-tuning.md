# T-050: Reconciler prompt tuning vs OpenAI image-gen cookbook (gpt-image only)

**Status:** TODO
**Sprint:** 3.5 follow-up（M3.5 開工前的品質投資）
**Est:** S
**Depends on:** T-015（reconciler 模組）、T-035（preview alias/motion 擴充）、T-045（gpt-5-mini 合約修正）
**Related:** 未來另開 i2v / Veo 端 prompt tuning 單

---

## Scope

把 OpenAI 官方 image-gen prompting guide 的指引**硬植入** reconciler 的 SYSTEM_PROMPT，並重排 final prompt 組裝順序、擴充 menu fragments、補強 alias 的 preservation 條款 —— 讓 reconciler 天生具備 cookbook 描述的 prompt-engineering 能力。**只動 gpt-image 路徑**（`create_base` / `create_base_with_ref` / `create_alias`）；i2v / motion 端的 prompt tuning 之後再開單處理。

**Source:** https://developers.openai.com/cookbook/examples/multimodal/image-gen-models-prompting-guide

**In scope:**

- `app/prompt/reconciler.py` SYSTEM_PROMPT 全面 rewrite —— 注入 cookbook 5 大原則（structure / photographic vocab / people hints / literal text / edit preservation）。原 rule 3「no creative bloat」拆成「可加技術語彙、不可加新主體/物件/場景」。SYSTEM_PROMPT 明確告知 LLM：principles 只套用於 gpt-image 三個 mode；motion mode 維持忠實翻譯。
- `app/prompt/menu_fragments.py` —— `style` 4 個選項從單行擴充成完整描述（含 lens / lighting / texture）。其他 category 不動。
- `api/platform_constraints.yaml` v1.1 → v1.2 ——
  - 新增 `base_creation_avoid` / `alias_creation_avoid` keys，存放「what to preserve / what to avoid」
  - alias_creation_avoid 補強保留條款（identity / geometry / proportions / pose / lighting）
  - **motion_creation 維持 v1.1 條目不動，不新增 motion_creation_avoid**（i2v tuning 之後另開單）
- `app/core/platform_constraints.py` —— dataclass 增加 `*_avoid` 欄位（含 motion 的，但 YAML 給空 → 空 tuple），loader 解析
- `app/prompt/constraints.py` —— 新增 `get_avoid_constraints_for_mode()`；motion 因 YAML 沒給 avoid → 回空 tuple，等同維持原行為
- `app/prompt/reconciler.py` `_compose_output` 重排：**scene → menu (subject) → reconciled note (details) → avoid**（cookbook 推薦順序）。`_compose_user_prompt` 把 scene + avoid 雙塊呈現給 LLM。Motion mode 的 avoid 段為空，組裝結果與原本相同。
- 測試：updates + 新增覆蓋 avoid 區塊位置、alias preservation
- 不發 PR（user 要先驗證）

**Not in scope:**

- **任何 motion / i2v 端的 prompt 改動** —— 之後另開單
- 改 `app/ai/gpt_image_2.py` 的 `input_fidelity="high"` flag（cookbook 對 identity-sensitive edit 的另一條建議；屬另一張單）
- 改 `app/ai/veo_3_1.py` 的呼叫參數
- Eval suite 跑分（planning/backend/prompt-reconciler.md §9.3 描述但 Phase 1 未建）
- Frontend 顯示變化（structure 在 backend 內部，UI 不用改）

---

## Planning refs

- `planning/backend/prompt-reconciler.md` §3 §5 §6 §7 —— 原 spec；本單在 §5 SYSTEM_PROMPT 上做大改、在 §3 platform constraints 上做 v1.2 additive 擴充
- OpenAI cookbook image-gen prompting guide —— 本單植入的權威來源
- `DECISIONS.md` —— gpt-5-mini 選型不變、JSON mode 不變

---

## Acceptance criteria

- [ ] SYSTEM_PROMPT 含：target-model context（gpt-image-2 / Veo 3.1）+ per-mode intent + cookbook 5 原則 + 原本的翻譯/衝突/JSON schema 規則 + 明確告知 motion mode 不套用 enrichment
- [ ] `platform_constraints.yaml` v1.2，含 `base_creation_avoid` + `alias_creation_avoid`（motion 不加 avoid），且 backward-compat（old `base_creation` 等仍存在）
- [ ] `menu_fragments.py` `style` 4 個選項展開成多句描述
- [ ] final prompt 組裝順序：scene → menu → user note → avoid（同一句點分隔）；motion mode avoid 段為空，等同原行為
- [ ] `applied_constraints` 在 ReconcileOutput 中保留 scene + avoid 全列（API response `platform_constraints` 仍含「transparent background」）
- [ ] Cache key 涵蓋 avoid 列表（YAML version bump 已自動 invalidate）
- [ ] 既有 reconciler / preview / motion 測試全綠（必要時更新斷言文字）
- [ ] 新增測試：base avoid block 位置、alias preservation
- [ ] `cd api && pytest tests/prompt_reconciler tests/prompt_preview tests/aliases tests/motions tests/checkpoints tests/routes/test_meta.py -q` 全綠
- [ ] `cd api && ruff check && ruff format --check && mypy --strict .` 全綠

---

## Files expected to touch

- `tickets/T-050-reconciler-prompt-tuning.md`（new）
- `api/platform_constraints.yaml`（edit; v1.2）
- `api/app/core/platform_constraints.py`（edit; dataclass + parser）
- `api/app/prompt/constraints.py`（edit; 新增 `get_avoid_constraints_for_mode`）
- `api/app/prompt/reconciler.py`（edit; SYSTEM_PROMPT、_compose_output、_compose_user_prompt、cache key）
- `api/app/prompt/menu_fragments.py`（edit; style 展開）
- `api/tests/prompt_reconciler/test_reconciler.py`（edit; updates + new tests）
- `STATUS.md`（edit）

---

## Notes

**Cache invalidation 自動：** SYSTEM_PROMPT 與 MENU_FRAGMENTS 都已被 `_logic_version` hash（reconciler.py:196-212）；YAML 改動透過 `version` 欄位進 cache key。本單所有 prompt 改動都會自動 shift cache key，**不需要 Redis flush**。

**測試 hard-coded 字串需要更新：**
- `test_menu_selection_order_isolates_cache_slots` 的 `"anime style, 2D illustration"` → 換成新 expanded 字串或改用 substring `"anime style"`
- `test_final_prompt_structure_constraints_then_menu_then_note` —— 本單擴充成 scene → menu → note → avoid 4 段順序檢查

**保持向下相容：**
- `applied_constraints` 仍是 flat list（scene + avoid 串接），所以 API response 的 `platform_constraints` 字串不會破。
- `alias_creation` 作為 scene 仍只有 `inherits base_creation rules` —— alias 的「preserve identity」往 `alias_creation_avoid` 移動，所以 `applied_constraints` 內仍會出現 `"preserves character identity..."`，既有測試（`test_alias_mode_includes_base_constraints`）通過。

**為什麼 v1.2 而非 v2.0：** 新增 keys 是 additive；舊 keys 都保留，沒有移除或重新詮釋既有條款。Cookbook 推薦的「scene → subject → details → avoid」結構是**程式組裝邏輯**的升級，不是 YAML schema 的不相容。
