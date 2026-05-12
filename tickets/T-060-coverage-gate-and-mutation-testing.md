# T-060: Coverage gate + mutation testing on critical modules

**Status:** TODO
**Sprint:** 3.5-pre（Harness pre-flight）
**Est:** S（3h，含 baseline 第一次跑）
**Depends on:** none
**Related:** T-054（dual-stack auth middleware；本單是 solo Phase 1 下的「自動第二雙眼睛」）

---

## Scope

CI 加 coverage gate 與 mutation testing baseline，鎖住 auth / errors / circuit 這三個高信任模組的 test 品質。

**In scope:**
- `pytest --cov-fail-under=<baseline>` 加進 PR CI（threshold 先用當前 baseline，不強衝 80%）
- `mutmut` 進 dev deps，初次跑 `app/core/errors.py` + `app/ai/circuit.py` + `app/auth/*` baseline
- mutmut 跑 nightly（不在 PR CI——一輪太慢），kill rate 跌破 `baseline - 5%` 自動開 issue
- baseline 數值寫進 `planning/harness/scope.md` 留紀錄

**Not in scope**（保留給其他單）：
- 全 codebase mutation（cost 不成正比）
- Frontend 端 mutation（stryker.js，B-tier 之後再說）
- 提高 coverage 到 100%（karpathy-guidelines 反 anti-pattern：會 incentivize agent 寫 dummy test）

---

## Planning refs（開工前必讀）

- `planning/harness/roadmap.md` §1 A3 — rationale 與門檻設定原則
- `CONTRIBUTING.md` §4.1 Phase 1 solo exception — 為什麼這條對 OAuth 系列重要

---

## Acceptance criteria

- [ ] `pyproject.toml` `[tool.pytest.ini_options]` 或 `[tool.coverage]` 設定 cov-fail-under
- [ ] PR CI backend job 加 cov gate，蓋過會 red
- [ ] mutmut 設定 + 初次 baseline 跑完，數值寫進 `planning/harness/scope.md` §2.5 附表
- [ ] Nightly mutmut workflow（可跟 T-058 的 workflow 共用 schedule 或獨立）
- [ ] Mutation drift 觸發時自動開 issue（label `mutation-drift`）
- [ ] `pytest --cov` 結果與 mutmut 結果都能本機重現（指令寫進 README）

---

## Files expected to touch

- `api/pyproject.toml` (edit) — 加 mutmut dev dep + cov-fail-under config
- `.github/workflows/pr.yml` (edit) — cov gate 條件
- `.github/workflows/mutation.yml` (new) — nightly mutmut + issue creation
- `planning/harness/scope.md` (edit) — 寫入 baseline 數值
- `README.md` (edit) — 加一行 mutation testing 本機重現指令

---

## OAuth scope required

n/a

---

## MCP tool delta

n/a

---

## Notes

- 為什麼門檻不直接設 80%：當前 coverage 可能 < 80%，硬設會立刻 fail；硬衝 80% 又會 incentivize 寫無意義 test（karpathy-guidelines anti-pattern）。**先 baseline，後續手動往上爬**。
- 為什麼選這三個模組做 mutation：`app/core/errors.py`（AgentError envelope 是 trust boundary）、`app/ai/circuit.py`（fail-safe 行為錯了沒人發現）、`app/auth/*`（M3.5 換 OAuth 之前先有 baseline，換完後對比看 mutation 行為是否 regressed）。
- mutmut baseline 第一次跑可能 30 分鐘起跳——不要在 PR CI 跑，nightly 跑。
- Mutation drift 不等於 bug：可能是 test 變寬鬆、可能是 dead code、可能是 mutmut 自身的 false positive。issue 要附 raw mutmut diff，由 author triage。
