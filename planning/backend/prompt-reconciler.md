# Character Foundry — Prompt Reconciler

> **Status:** Draft v0.1 · 2026-04-23
> **Owner:** Backend Agent
> **Based on:** `../product/functional-scope.md` §8 語言策略、M1 衝突處理

---

## 1. 職責

統一處理**使用者輸入 → 最終 AI prompt** 的轉換，單一模組服務所有生成流程：

- 建立 Base（Checkpoint）
- 建立 Alias
- 建立 Motion

**三合一職責：**
1. **翻譯**：中文輸入 → 英文 prompt
2. **衝突解析**：使用者補述違反平台 constraints 時，重寫補述
3. **組合**：選單 fragments + 補述 + 平台 constraints → 一段連貫的英文 prompt

---

## 2. 介面契約

```python
from dataclasses import dataclass
from enum import Enum

class ReconcileMode(str, Enum):
    CREATE_BASE = "create_base"       # text2image, no reference
    CREATE_BASE_WITH_REF = "create_base_with_ref"  # 模式 B 參考圖
    CREATE_ALIAS = "create_alias"     # image2image / inpaint / mixed
    CREATE_MOTION = "create_motion"   # i2v

@dataclass
class ReconcileInput:
    mode: ReconcileMode
    menu_selections: dict[str, str] | None  # 例 {"gender": "female", "style": "ink_wash"}
    freeform_note: str | None  # Chinese
    has_reference_image: bool
    has_inpaint_mask: bool

@dataclass
class ReconcileOutput:
    final_prompt: str                # 最終英文 prompt
    reconciled_note_en: str          # 補述英譯版（已解衝突）
    menu_fragments_en: list[str]     # 選單對應的英文片段
    applied_constraints: list[str]   # 實際注入的平台 constraints
    removed_segments: list[RemovedSegment]  # 從使用者輸入剔除的內容
    llm_latency_ms: int
    cached: bool

@dataclass
class RemovedSegment:
    original_zh: str
    reason: str  # 例："conflicts with transparent_background constraint"

class PromptReconciler:
    async def reconcile(self, input: ReconcileInput) -> ReconcileOutput: ...

    async def preview(self, input: ReconcileInput) -> ReconcileOutput:
        """同 reconcile，但不寫 cache、不 log。供 UI 的進階檢視按鈕用。"""
        ...
```

---

## 3. 平台固定 Constraints（v1）

由 backend 維護在 `platform_constraints.yaml`，每次改動 version bump：

```yaml
version: v1
updated_at: 2026-04-23

base_creation:
  - transparent background
  - character centered in frame
  - character facing camera directly (正面)
  - full body shot unless specified otherwise
  - consistent neutral lighting
  - no watermarks or signatures

alias_creation:
  - inherits base_creation rules
  - preserves character identity from base image

motion_creation:
  - transparent background (match base)
  - camera remains stationary
  - character stays centered
  - smooth, realistic motion
  - no sudden frame jumps or distortions
  - duration 3-6 seconds for preset, 3-10 for custom
```

**Version bump rules：**
- 改任何 constraint → minor version（v1 → v1.1）
- 加新 constraint → major（v1 → v2）
- `GenerationLog` 記錄當時的 `constraint_version`，用於追溯

---

## 4. LLM 選型

**Phase 1 選擇：gpt-5-mini**（via OpenAI API）

**理由：**
- 翻譯 + 結構化重寫任務對這個 size 已足夠，不需要大模型
- 成本低（每次 < 0.01 unit，對比 gpt-image-2 10x 便宜）
- 延遲短（1-2s），對使用者感受最重要
- 共用 `OPENAI_API_KEY`，少一組 secret 要管
- JSON mode 穩定

**替代方案：**
- Claude Sonnet / gpt-5：品質略高但這任務用不到，成本與延遲都划不來
- Local LLM（Qwen / DeepSeek）：省 API 成本，但 Phase 1 不值得架設

**配置：**
```
OPENAI_API_KEY=<env>            # 與 gpt-image-2 共用
RECONCILER_MODEL=gpt-5-mini
RECONCILER_TIMEOUT_MS=30000
RECONCILER_MAX_TOKENS=800
```

---

## 5. Reconciler System Prompt（核心 prompt）

```
You are a prompt engineering assistant for an AI character generation platform.

Your job: given (1) user menu selections, (2) user freeform note in Chinese, and
(3) platform-level fixed constraints, produce a single coherent English prompt
suitable for gpt-image-2 / Veo 3.1.

RULES:
1. Translate freeform Chinese note to natural English.
2. Identify any user-intent that conflicts with platform constraints. Rewrite
   the conflicting parts to respect the constraint. Record what was removed
   and why.
3. Compose: platform_constraints + menu_fragments + reconciled_user_note
   into a single flowing prompt. Order: scene constraints first, then
   character attributes, then user-specific details.
4. Do NOT add content the user did not imply (no creativity bloat).
5. If user note is empty, just compose constraints + menu fragments.
6. Return structured JSON matching the schema below.

OUTPUT SCHEMA:
{
  "final_prompt": "...",
  "reconciled_note_en": "...",
  "menu_fragments_en": ["..."],
  "applied_constraints": ["..."],
  "removed_segments": [
    {"original_zh": "...", "reason": "..."}
  ]
}
```

Input（user message，Python template）：

```
Mode: {mode}
Has reference image: {has_reference_image}
Has inpaint mask: {has_inpaint_mask}

Menu selections:
{menu_selections_pretty}

User freeform note (Chinese):
{freeform_note}

Platform constraints (must respect):
{platform_constraints_list}

Menu fragment mappings (use these English values for selected options):
{menu_to_english_mapping}
```

---

## 6. 選單 → Prompt Fragment 對照（Phase 1 雛形）

存 `menu_fragments.yaml`：

```yaml
gender:
  male: "adult man"
  female: "adult woman"
  nonbinary: "androgynous person"

age:
  child: "child, age 8-12"
  teen: "teenager, age 14-18"
  young_adult: "young adult, age 20-30"
  middle_aged: "middle-aged, age 40-55"
  elderly: "elderly, age 60+"

eye_shape:
  large_round: "large, round expressive eyes"
  almond: "almond-shaped eyes"
  upturned: "upturned cat-eyes"
  # ...

style:
  realistic: "photorealistic portrait"
  anime: "anime style, 2D illustration"
  ink_wash: "traditional Chinese ink wash painting style"
  watercolor: "soft watercolor illustration"
  # ...
```

**選單實際內容由 Product + UX 後續填（M5）**；此檔是 backend 對應的格式。

---

## 7. Caching 策略

**目的：** 使用者迭代時（點「重試」「進階檢視」）同樣輸入會重複出現，省 LLM 呼叫。

**Cache key：**
```python
key = sha256(
    mode + "|" +
    sorted_json(menu_selections) + "|" +
    freeform_note + "|" +
    str(has_reference_image) + "|" +
    str(has_inpaint_mask) + "|" +
    constraint_version
)
```

**儲存：** Redis（task queue 本來就有，共用），TTL 24h

**Cache miss 時：** 呼叫 LLM、寫入 cache、回傳

**注意：** 「進階檢視」預覽呼 `preview()` 方法 → 走 cache 讀但**不寫 cache**（避免 preview 污染正式 cache）

---

## 8. 錯誤處理

| 情境 | 行為 | 回傳錯誤代碼 |
|---|---|---|
| LLM timeout (30s) | Fallback：只翻譯，不解衝突（僅 append constraints，讓 image model 自己處理）| - (success but with `degraded: true` flag) |
| LLM content policy 拒絕 | 把使用者補述標記為 toxic，回 error | `PROMPT_CONTENT_POLICY` |
| LLM JSON 解析失敗 | 重試 2 次；仍失敗則 fallback | `PROMPT_RECONCILE_FAILED` |
| 連續 5 次失敗（circuit open）| 暫時跳過 reconciler，直接拼 constraints + freeform_note（中文送模型）| Logged warning，UI 顯示「部分功能暫時不可用」 |

**Degraded mode：** 如果 OpenAI API 掛掉（或 reconciler 連續失敗觸發 circuit breaker），reconciler 降級為「只翻譯，不衝突解析」。使用者補述衝突 constraints 時，image model 通常 constraint 會贏（因為在 prompt 結尾重述）。品質下降但不 block 使用。

---

## 9. 測試策略

### 9.1 單元測試（結構測試）

```python
def test_reconcile_removes_conflicting_background():
    input = ReconcileInput(
        mode=ReconcileMode.CREATE_BASE,
        menu_selections={"gender": "female", "style": "ink_wash"},
        freeform_note="在雜亂的市場背景中",
        has_reference_image=False,
        has_inpaint_mask=False,
    )
    output = await reconciler.reconcile(input)

    assert "transparent background" in output.final_prompt.lower()
    assert "cluttered market" not in output.final_prompt.lower()
    assert len(output.removed_segments) >= 1
    assert any(
        "background" in seg.reason.lower()
        for seg in output.removed_segments
    )
```

### 9.2 Golden file 測試（回歸測試）

固定 ~20 組代表性輸入，比對輸出結構穩定性。內容細節可以變，但 schema 不能壞。

### 9.3 Eval suite（品質測試）

100 組標記輸入，跑完算：
- 翻譯品質（BLEU score vs 人工譯本）
- 衝突偵測 recall（有多少該偵測的有偵測到）
- 衝突偵測 precision（偵測到的有多少是真衝突）

切換 LLM 或 system prompt 時跑，確保品質不退化。

---

## 10. 關聯文件

- `api-shape.md` §5.6 Prompt Preview endpoint
- `task-queue.md` reconciler 呼叫在 task worker 內
- `ai-integration.md` reconciler 輸出被 gpt-image-2 / Veo client 消費
- `../product/functional-scope.md` F-04a、§8
