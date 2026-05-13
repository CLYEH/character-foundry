# T-066: Provider contract replay 改為手動 only（停 nightly）

**Status:** DONE
**Sprint:** 3.5-pre（Harness pre-flight follow-up）
**Est:** XS（15min）
**Depends on:** none
**Related:** T-058（建立此 sensor）/ T-064（dedupe by signature，本單變更後 priority 下調）

---

## Scope

T-058 nightly `provider-contract.yml` 改為 `workflow_dispatch` only，拿掉 `schedule:` block。每日 cron 估月成本 ~$10（Veo 占 ~$9）；對單人內部專案，drift 偵測頻率不值這個 burn rate。改成有需要時手動觸發 / push code 動到 ai client 時手動跑一次驗。

**In scope:**
- `.github/workflows/provider-contract.yml` — 移除 `on.schedule`，保留 `on.workflow_dispatch`
- `planning/devops/operations.md` §7 — 從「Nightly」改為「On-demand」，月成本估算改成「per manual run」
- `planning/harness/scope.md` / `roadmap.md` — A1 sensor 描述同步
- `STATUS.md` — T-058 entry 同步（「nightly」字樣 → 「manual real-provider contract replay sensor」），known-risks 不變
- Close `provider-drift` issue #83（cron 不再 fire，dedupe window 72h 內也不會再被 touch；issue 留著只是噪音）

**Not in scope**（保留給其他單）：
- 改 contract test 本身（drift 偵測邏輯不變）
- 把 Veo 拆獨立 schedule 跑（後續若想保留部分 nightly 偵測再開）
- T-064（signature-based dedupe）— 改 manual 後 dedupe pressure 下降，priority 自然下調但不關 ticket

---

## Planning refs（開工前必讀）

- `planning/devops/operations.md` §7（Provider contract replay 維運 SOP）— 整段要改
- `planning/harness/scope.md` §2.1 / `roadmap.md` §1 A1 — sensor 框架定位
- `.github/workflows/provider-contract.yml` — workflow 本體

---

## Acceptance criteria

- [x] `provider-contract.yml` 不再有 `schedule:` block，`workflow_dispatch:` 仍在
- [x] `gh workflow run provider-contract.yml` 手動觸發路徑可走（dry-run 不需真跑，但 `gh workflow list` 看得到、UI 上「Run workflow」按鈕還在）
- [x] `planning/devops/operations.md` §7 描述對齊（標題、cost 估算、觸發時機）
- [x] `planning/harness/scope.md` / `roadmap.md` A1 描述對齊
- [x] `STATUS.md` 對 T-058 / T-060 描述中「nightly」字眼同步
- [ ] Issue #83 close 並留 comment 連到本 ticket / merge commit（post-merge）
- [x] 沒動到 `test_real_provider_contract.py` 或 `addopts = -m "not real_provider"` 設定（contract test 程式碼不變）

---

## Files expected to touch

- `.github/workflows/provider-contract.yml` (edit) — remove `schedule:`
- `planning/devops/operations.md` (edit) — §7 rewording
- `planning/harness/scope.md` (edit) — A1 sensor 描述
- `planning/harness/roadmap.md` (edit) — A1 描述
- `STATUS.md` (edit) — T-058 / T-060 update line 同步「manual」cadence
- `tickets/T-066-provider-contract-manual-only.md` (new) — 本檔

---

## OAuth scope required

n/a — pure infra / docs change，沒動 endpoint。

---

## MCP tool delta

n/a — 沒動 MCP registry。

---

## Notes

- 觸發時機建議：動到 `app/ai/gpt_image_2.py` / `app/ai/reconciler.py` / `app/ai/veo_3_1.py` 或它們的 `_parse_*` 函式時，PR open 後手動 `gh workflow run provider-contract.yml` 跑一次驗 shape。這個 norm 之後可考慮做成 PR check（path filter + 自動 dispatch），但本單不做。
- T-058 sensor value 沒消失，只是 trigger 模型從 push-based 變成 pull-based。drift 真的發生時人類介入時間多了幾天 lag，但對「provider schema 改了通常持續存在」的失敗模式來說，lag 一週內可接受。
- 退路：之後若改回 nightly 或週次，直接加回 `schedule:` 即可，contract test 本體完全沒動。
- 開 issue #83 close comment 時順手連到本 ticket merge PR，retro 才有 trail。
