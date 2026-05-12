# T-065: PR CI guard — `[tool.mutmut]` change must bump `.harness/mutation-baseline.json`

**Status:** TODO
**Sprint:** Harness B-tier（M3.5 ship 後再排 — 但**不 block** Sprint 3.5a，可隨時插單）
**Est:** XS（30 min）
**Depends on:** T-060（baseline JSON + nightly workflow）
**Related:** T-059（同類 architecture fitness sensor 的思維 — 把規則寫成 enforcement 不是 doc）

---

## Scope

T-060 落地後，mutmut baseline (`baseline_kill_rate: 0.7984`) 鎖在 `app/core/errors.py` + `app/ai/circuit.py` + 8 個 `tests/ai/*` + 1 個 `tests/prompt_reconciler/*` 的具體組合。**這個組合一動，baseline 必須一起重算**，不然隔天 nightly 一定 false-alarm 開 `mutation-drift` issue。

目前這條規則只寫在三處 doc / comment：

- `.harness/mutation-baseline.json` 的 `notes` 欄位
- `api/pyproject.toml` `[tool.mutmut]` 區塊註解（隱性，沒明寫「改我要 re-baseline」）
- MemPalace `character-foundry/harness-decisions` drawer（2026-05-12 saved）

三處都是「**下個 agent 剛好讀到才有用**」—— 不是強 enforcement。本單把它變成 structural sensor。

**In scope:**
- 新增 `api/scripts/check_baseline_resync.py`（~30 行）：parse PR diff，若 `api/pyproject.toml` `[tool.mutmut]` block 改了 **且** `.harness/mutation-baseline.json` 沒同步改動 → exit 1 with actionable message
- 比對「`[tool.mutmut]` block 改了」的方式：用 toml 解析 + 算 `paths_to_mutate` / `tests_dir` / `mutate_only_covered_lines` / `also_copy` 四個 key 的 hash；hash 不一樣就算 baseline-sensitive change
- 同樣方式偵測 `mutmut` dev dep 版本變動（`[project.optional-dependencies] dev` 裡的 `mutmut>=X.Y`）
- `.github/workflows/pr.yml` backend job 加一個 step 跑這個 script；失敗 → red CI
- Script 印 actionable hint：「你動了 `[tool.mutmut]` config 但沒 update `.harness/mutation-baseline.json`。要嘛 (a) 跑一次 `mutmut run` 取新 kill rate 並 commit 進 baseline JSON，要嘛 (b) 若你確定 baseline 不該變（e.g. 只改了 comment），加 `# baseline-irrelevant` 註記在那次 commit message」
- 提供 escape hatch：commit message 含 `baseline-irrelevant` 字串時 script skip（給 comment-only / formatting-only 改動用）

**Not in scope**（保留給其他單）：
- 自動 re-run mutmut 算新 kill rate（半小時起跳，PR CI 跑不動）— 維持「人類 / agent 動 config 時自己跑」的契約
- 偵測 `app/core/errors.py` / `app/ai/circuit.py` 程式邏輯改動 → 那是 nightly 的職責，不是 PR-time guard
- Frontend coverage gate（A3 ticket 不含 frontend）

---

## Planning refs（開工前必讀）

- `planning/harness/scope.md` §2.6（Lifecycle distribution）— 這條歸 **PR CI** 階段，跟 T-059 layering test 同層
- `planning/harness/roadmap.md` §1 A2 / A3 — T-059 / T-060 sensor pattern 是這條的範本
- `tickets/DONE/T-060-coverage-gate-and-mutation-testing.md` Acceptance criteria — 對應 baseline 機制細節
- `.harness/mutation-baseline.json` `notes` 欄 — 當前 doc 版規則
- `api/pyproject.toml` `[tool.mutmut]` block — 受監控的設定區段

---

## Acceptance criteria

- [ ] `api/scripts/check_baseline_resync.py` 存在，CLI 介面：`python check_baseline_resync.py --base-ref origin/main`（從 `git diff <base-ref>...HEAD` 取 diff）
- [ ] 正向 case：手動跑 `python check_baseline_resync.py --base-ref origin/main` 在「動 `paths_to_mutate` + 同 PR 改 baseline JSON」的 commit 上，exit 0
- [ ] 反向 case：在「動 `paths_to_mutate` 但**沒**動 baseline JSON」的 fake commit 上 exit 1，stderr 印 actionable hint 含具體該跑哪條 `mutmut run`
- [ ] Escape hatch：commit message 含 `baseline-irrelevant` 時 exit 0（即使 `[tool.mutmut]` 在 diff 裡）
- [ ] `.github/workflows/pr.yml` backend job 加 step：`python api/scripts/check_baseline_resync.py --base-ref origin/${{ github.event.pull_request.base.ref }}`；失敗 red CI
- [ ] 該 step 在 `pip install -e ".[dev]"` 之後跑（script 用 stdlib only，但保險）
- [ ] mypy --strict + ruff 都過
- [ ] PR 本身的 e2e green（這條的 PR 自己也會被新 sensor 檢查 — `[tool.mutmut]` 沒動，baseline 沒動，應該綠）

---

## Files expected to touch

- `api/scripts/check_baseline_resync.py` (new) — ~30-40 行 Python
- `.github/workflows/pr.yml` (edit) — 加一個 backend job step
- `STATUS.md` (edit) — 開單時 + 完成時都要動
- 開單時也順手清掉 `.harness/mutation-baseline.json` `notes` 欄裡「靠 doc enforce」那段 — 改寫成「enforce 在 PR CI（T-065）」

---

## OAuth scope required

n/a（純 harness sensor，不碰 endpoint）

---

## MCP tool delta

n/a（純 harness sensor，不碰 agent surface）

---

## Notes

**為什麼歸 B-tier 不是 3.5-pre：** 這條 sensor 預防的是「忘記 re-baseline → false drift issue」這條 operational annoyance，**不是** OAuth 落地的 structural prerequisite。Sprint 3.5a 不應因為這條卡住。

**為什麼還是要做：** T-054 dual-stack auth middleware 落地時很可能順手調 `tests/auth/conftest.py` 或 `tests/auth/*` 結構 → 也許會啟動「auth/* 進 mutation scope」這條（見 STATUS.md backlog row S3.5-2）。那時候 `[tool.mutmut]` 一定會被動到。有 T-065 在，T-054 PR 上沒 update baseline 會被 PR CI 擋下；沒 T-065 的話要等隔天 nightly false alarm 才知道。

**Implementation hint — escape hatch 設計：** `baseline-irrelevant` magic string 走 commit message body 而不是 PR title。理由：(a) squash merge 時 PR title 變 commit subject，body 內容反而最後一個 commit 的 message — 不穩定。(b) PR title 上加 magic string 也容易被作者拿去當 marketing copy 一部分，誤用率高。Script 應該 walk `git log --format=%B <base-ref>..HEAD` 看全部 commits 的 message，任何一個 commit 含 `baseline-irrelevant` 就 pass。

**Implementation hint — 計算 hash 的細節：** 用 `tomllib`（py3.11+ stdlib）讀 `api/pyproject.toml` 兩次（base + head），取 `data["tool"]["mutmut"]`，serialize 成 canonical form（`json.dumps(..., sort_keys=True)`），hash compare。**順序變動不算 baseline 變動**（e.g. 重排 `paths_to_mutate` 順序，set 等價）。但**內容變動**（加 / 拿掉 module、改 `mutate_only_covered_lines` 從 true 到 false）會 trip。

**Implementation hint — mutmut 版本變動的偵測：** 同樣 toml 解析，取 `[project.optional-dependencies].dev`，grep `^mutmut`，若兩次 spec 字串不同（`>=3.0` → `>=3.5` 算動，`>=3.0` 重排到 list 不同位置不算動）就 trip。

**反 enforcement 的合理 trade-off：** 如果這條 sensor 經常 false-fire（e.g. 純 typo 修正觸發），加 escape hatch 比加更多 logic 好。`baseline-irrelevant` 是顯式的 author 聲明，author 為這個聲明負責。
