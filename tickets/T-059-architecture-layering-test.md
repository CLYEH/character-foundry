# T-059: Architecture fitness — layering / import-direction test

**Status:** TODO
**Sprint:** 3.5-pre（Harness pre-flight）
**Est:** S（3h）
**Depends on:** none
**Related:** T-054 / T-055（M3.5 加 `app/mcp/` + `app/auth/` 兩個新 layer，本單先 lock baseline）

---

## Scope

加 architecture fitness test 斷言既有 layer 之間 import 方向不被 silently 穿透；同時為 M3.5 即將加的 `app/mcp/` + `app/auth/` 預埋規則。

**In scope:**
- `import-linter` 進 dev dep（或自寫 30-50 行 `ast` walk，看哪個簡單）
- 規則：
  - `app/api/routes/*` 不可直接 import `app/models/*`（要走 `app/repositories/` 或 `app/schemas/`）
  - `app/ai/*` 不可 import `app/api/*`（單向：API 用 AI，AI 不知道 API）
  - 未來 `app/mcp/*` 與 `app/auth/*` 必須共用同一個 scope source（**不可各自硬編 scope 字串**——本單先寫 placeholder rule，T-053/T-054 落地時 enable）
- pytest 整合：`api/tests/arch/test_layering.py` 用 pytest 跑，PR CI 一起 fail
- 違規時 error message 寫 LLM-friendly：「`app/api/routes/foo.py` 不可直接 import `app.db.models.Character`，改 import `app.repositories.character.get_character`」

**Not in scope**（保留給其他單）：
- Frontend 端的 layering test（web/ 那層，未來 C-tier 一起）
- Performance fitness（latency / throughput SLO）— 不同概念
- 既有 violation 修復——本單先把 test 加上去；既有 violation 列在 ignore list（with TODO），逐單清理

---

## Planning refs（開工前必讀）

- `planning/harness/roadmap.md` §1 A2 — 規則清單與 M3.5 耦合
- `planning/harness/scope.md` §2.5 — Architecture fitness 覆蓋度（目前是零）

---

## Acceptance criteria

- [ ] `import-linter` 或 ast walk script 加進 `api/tests/arch/`
- [ ] 三條規則跑得起來：`app/api/routes/*` 不可 import `app/models/*` / `app/ai/*` 不可 import `app/api/*` / 未來 `app/mcp/*` 與 `app/auth/*` 共用 scope source
- [ ] 既有 violation（如果有）列入 ignore list + 每條附對應 follow-up ticket 或 STATUS.md backlog 行
- [ ] PR CI（`.github/workflows/pr.yml` backend job）跑這條 test，違規會 red
- [ ] Error message 包含「**應該怎麼改**」的 actionable 指引（非單純「violation」）
- [ ] `pytest api/tests/arch/ -v` 在 clean state 全綠

---

## Files expected to touch

- `api/pyproject.toml` (edit) — 加 `import-linter` 到 dev deps（如果採用）
- `api/.importlinter` 或 `api/pyproject.toml` `[tool.importlinter]` section (new) — 規則設定
- `api/tests/arch/__init__.py` (new)
- `api/tests/arch/test_layering.py` (new) — pytest wrapper
- `.github/workflows/pr.yml` (edit, optional) — 如果不靠 pytest 自然跑到的話加一步
- `planning/harness/scope.md` (edit) — 更新 §2.5「Architecture fitness：零 → 部分」

---

## OAuth scope required

n/a（純 harness 工，無新增 endpoint）

---

## MCP tool delta

n/a（無 agent surface 影響）

---

## Notes

- 先評估：`import-linter` vs 自寫 ast walk。`import-linter` 配置簡單但多一個 dep；ast walk 50 行可控。**建議用 import-linter**——它已經有 contract types `forbidden` / `layers` / `independence`，省輪子。
- mcp / auth 的 scope source rule 現在還 enable 不了（目錄不存在）——本單寫 placeholder 規則 + commented out 條目，T-054 / T-055 進來時 un-comment。
- 既有 violation 偵測：第一次跑可能爆一堆，逐條判斷是 real bug 還是 historical accident。`ignore_imports` 條目要寫成 inline comment 解釋為什麼（不然下次 reviewer 一定問）。
- Error message LLM-friendly 範例：`"Forbidden import: 'app.api.routes.character' imports 'app.models.character.Character'. Routes must access models through 'app.repositories.character.*' (returns Pydantic schemas) — see CONTRIBUTING §x."`
