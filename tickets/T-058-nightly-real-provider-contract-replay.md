# T-058: Nightly 真 provider contract replay sensor

**Status:** TODO
**Sprint:** 3.5-pre（Harness pre-flight）
**Est:** S（4-6h）
**Depends on:** none
**Related:** T-042 / T-045 / T-051（三條同款 provider drift 失敗模式，本單是 systematic fix）

---

## Scope

加 nightly GitHub Actions job 對 gpt-image-2 / gpt-5-mini / Veo 3.1 三個 provider 各跑最便宜的真 call，**只斷言 response shape**，shape drift 自動開 ticket。

**In scope:**
- pytest marker `@pytest.mark.real_provider`（pyproject.toml 註冊 marker）
- 三個 contract replay test：gpt-image-2 / gpt-5-mini / Veo 3.1，各一個最小 call，使用 fixed minimal prompt
- 斷言對象：response shape（必填欄位存在 + 型別正確），**不**斷言內容語意
- 新 GitHub Actions workflow `.github/workflows/provider-contract.yml`，cron schedule（UTC 00:00 nightly），手動 `workflow_dispatch` 也能跑
- GH Actions secrets 設定：每個 provider 用獨立 test API key（不共用 dev key）+ 文件記載 spending cap 設定步驟
- 失敗時 workflow 自動建立 issue（label `provider-drift`），不擋 PR CI

**Not in scope**（保留給其他單）：
- LLM-as-judge 對輸出品質評分（B6，未來另開）
- Real provider 對 prompt assembly 結果的端到端 e2e（B5 prompt snapshot 自己處理）
- Cost monitoring dashboard（DevOps agent 範圍）

---

## Planning refs（開工前必讀）

- `planning/harness/roadmap.md` §1 A1 — 完整 rationale + 預估
- `planning/harness/scope.md` §3 第 2 條 — 失敗模式背景

---

## Acceptance criteria

- [ ] `pytest -m real_provider` 在 local 用真 key 跑得起來（手動驗證）
- [ ] 三個 contract replay test 各自能獨立 fail 出 shape 不符的場景（mock fixture 模擬 drift 確認偵測到）
- [ ] `.github/workflows/provider-contract.yml` nightly schedule 設定正確（用 `workflow_dispatch` 手動跑一次驗證）
- [ ] Workflow fail 時 issue 自動開出（label `provider-drift`）
- [ ] README / DEVOPS 對應頁面記載：test API key 怎麼註冊、spending cap 怎麼設、issue 出現時的 triage SOP
- [ ] PR CI（既有 `pr.yml`）不受影響——`-m real_provider` 在 default `pytest` 跑時自動 skip

---

## Files expected to touch

- `api/pyproject.toml` (edit) — 註冊 marker，default `pytest` 設定 `-m "not real_provider"` skip
- `api/tests/ai/test_real_provider_contract.py` (new) — 三個 test case
- `.github/workflows/provider-contract.yml` (new) — nightly schedule + workflow_dispatch + issue creation step
- `planning/devops/operations.md` (edit) — 加 §「Provider contract replay 維運 SOP」
- `README.md` 或對應 onboarding doc (edit) — 加一行說明 test API key 由誰管理

---

## OAuth scope required

n/a（純 CI / harness 工，無新增 endpoint）

---

## MCP tool delta

n/a（無 agent surface 影響）

---

## Notes

- API key 不共用：gpt-image-2 / gpt-5-mini / Veo 3.1 各開獨立 test 帳號或 project，spending cap 設 $5/month。**不要用 dev key**，會把 dev quota 吃掉。
- shape assertion 寫法建議：`response.json()["videos"]` 不檢查內容，只檢查 `isinstance(list)` + len ≥ 1；T-051 RAI filter 場景刻意保留為 valid pass case（沒 videos field 是 RAI shape，不算 contract drift）。
- 失敗 issue 模板要附 raw response 全文（mask API key），方便快速判讀是真 drift 還是 provider 暫時 5xx flake。
- 第一次跑會 establish baseline——baseline 跑出來後加 commit 把 expected shape 寫死。
