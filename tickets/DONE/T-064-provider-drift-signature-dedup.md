# T-064: Provider-drift issue dedup by failure signature

**Status:** TODO
**Sprint:** Harness B-tier (post-M3.5)
**Est:** XS (1-2h)
**Depends on:** T-058（landed）
**Related:** T-058（this extends its dedup logic）

---

## Scope

The dedup logic that T-058 added to `.github/workflows/provider-contract.yml` looks up an open `provider-drift` issue touched (created or commented) within a 72h `updated_at` window — but doesn't distinguish *which* test/provider failed. Codex PR #76 review round-3 flagged that two distinct failures within that window (e.g., OpenAI schema drift one day, Veo outage two days later) would collapse into one thread, hiding the second incident.

This ticket composes a **failure signature** (test name, which is 1:1 with provider) on top of the existing time+state window so distinct provider failures stay as distinct issues even when they overlap in time.

**In scope:**
- Extract failing test names from `log_snippet.txt` (`api/tests/ai/test_real_provider_contract.py::test_gpt_image_2_real_response_shape`, `…::test_gpt_5_mini_real_response_shape`, `…::test_veo_3_1_real_response_shape`) — pytest's `-rA` summary prints these.
- Use the test name set as a signature when looking up existing open issues. Match → comment; new signature → fresh issue.
- Carry the signature in the issue title (e.g., `[provider-drift][veo-3.1] …`) so visual triage is faster.
- One open issue per provider per active drift; multiple providers drifting in overlapping time windows = N issues.
- Compose with the existing 72h `updated_at` lookback (T-058 c8e0a60): match requires both signature AND active time window.

**Not in scope**（保留給其他單）：
- LLM-as-judge for output quality（B6 in roadmap）— different sensor entirely.
- Auto-close stale `provider-drift` issues — manual close per SOP §7.4 is fine for now.

---

## Planning refs（開工前必讀）

- `planning/harness/roadmap.md` §1 A1 — original A-tier rationale for the sensor
- `planning/devops/operations.md` §7.4 — current triage SOP
- `.github/workflows/provider-contract.yml` — T-064 TODO comment points here

---

## Acceptance criteria

- [x] `actions/github-script` step parses failing test names out of `log_snippet.txt`（`-rA` summary `FAILED/ERROR …::test_*` + FAILURES-section `____ test_* ____` header，後者當 60 KB 截斷把 summary 切掉時的 fallback）
- [x] Issue title carries provider tag derived from the failing test (`[provider-drift][gpt-image-2]`, etc.); fallback to `[unknown]` if parsing fails
- [x] Dedup looks up open `provider-drift` issues whose title contains the same provider tag — match → comment, no match → create。**窗用 72h（非 AC 寫的 24h）**：依 Scope §22「compose with the existing 72h `updated_at` lookback」，沿用 T-058 既有 72h 常數只疊上 signature 維度，不動窗大小（surgical）。
- [ ] Manual `workflow_dispatch` run that intentionally fails two providers in one call (e.g., expired keys on two providers simultaneously) confirms two separate issues are filed — **Manual**：需 live GitHub Actions run 才能驗；解析/分流邏輯已用 node harness 本地驗（單一/雙 provider/截斷 fallback/PR 過濾/per-tag 窗）全綠
- [x] Workflow yaml passes the same `python -c "import yaml; yaml.safe_load(...)"` sanity check used in T-058

---

## Files expected to touch

- `.github/workflows/provider-contract.yml` (edit) — extend the github-script step
- `planning/devops/operations.md` §7.4 (edit) — note that issues now carry provider tags

---

## OAuth scope required

n/a（CI workflow only）

---

## MCP tool delta

n/a

---

## Notes

- Tradeoff against the simpler current dedup: per-signature adds a parsing step. If `log_snippet.txt` doesn't contain recognisable test names (e.g., the failure happened before pytest ran), fall back to the time-based dedup with `[unknown]` tag.
- Same-day distinct-provider failures are uncommon (one nightly run + manual dispatches), so this is polish, not a correctness fix. T-058's PR (#76) deferred this round-3 Codex feedback for that reason.
- Signature could later expand to include error-fingerprint (e.g., first line of the `_drift` message), but test name is sufficient v1 — coarse enough to dedup persistent failures, fine enough to separate provider events.
