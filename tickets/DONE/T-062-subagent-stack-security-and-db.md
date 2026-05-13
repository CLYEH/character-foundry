# T-062: Subagent stack — security-engineer + db-optimizer

**Status:** TODO
**Sprint:** 3.5-pre（Harness pre-flight）
**Est:** XS-S（2h）
**Depends on:** none
**Related:** T-054 / T-055（OAuth dual-stack + refresh_token schema 直接受益）

---

## Scope

Fork `agency-agents` 的 `security-engineer` + `db-optimizer` 兩個 subagent 進 `.claude/agents/`，並改 pre-push hook 對 security-sensitive / schema-migration ticket 自動 chain。

**In scope:**
- `.claude/agents/security-engineer.md`（fork）
- `.claude/agents/db-optimizer.md`（fork）
- `.claude/hooks/pre-push-review.sh` 改造：依 ticket file 內容判斷是否 chain 額外 subagent
- 判斷依據：讀 `tickets/T-XXX-*.md` 的 metadata（沒有的話用 branch name 提取 T-XXX，再 grep 是否 security-sensitive / schema migration 關鍵字）
- `.githooks/pre-push` 同步加 directive（兩個 hook 是 mirror）

**Not in scope**（保留給其他單）：
- llm-output-judge subagent（B6 那條）
- 改 `engineering-code-reviewer` 既有定義（保持不動）
- subagent 之間互相 delegation 機制

---

## Planning refs（開工前必讀）

- `planning/harness/roadmap.md` §1 A5 — 為什麼從 B 升 A
- `CONTRIBUTING.md` §4.1 + §4.4 — 既有 reviewer 角色與 AI agent 輔助說明
- `.claude/agents/engineering-code-reviewer.md` — 既有 subagent 結構參考

---

## Acceptance criteria

- [ ] 兩個 subagent `.md` 加進 `.claude/agents/`，frontmatter 完整（name / description / tools）
- [ ] pre-push hook 識別 security-sensitive / schema migration ticket 後 prompt Claude chain 對應 subagent
- [ ] 判斷邏輯有測試：對 T-052（infra）/ T-053（security）/ T-055（schema migration）/ T-056（frontend）各跑一次手動模擬，確認 chain 行為正確
- [ ] `CONTRIBUTING.md` §4.4 更新清單把兩個新 subagent 列上去
- [ ] 既有 `engineering-code-reviewer` 流程不被破壞（單純加 chain，不取代）

---

## Files expected to touch

- `.claude/agents/security-engineer.md` (new)
- `.claude/agents/db-optimizer.md` (new)
- `.claude/hooks/pre-push-review.sh` (edit) — 加 ticket file 判讀 + chain 邏輯
- `.githooks/pre-push` (edit) — 同步 directive
- `CONTRIBUTING.md` (edit) — §4.4 清單更新

---

## OAuth scope required

n/a

---

## MCP tool delta

n/a

---

## Notes

- Fork 來源：https://github.com/msitarzewski/agency-agents（既有 `engineering-code-reviewer` 也 fork 自這裡）。改名遵循既有慣例：保留原名或加 prefix 都行，但要在檔案最後標明 source。
- 判讀邏輯建議：
  - 從 `$range`（hook 拿到的 ref range）取 branch name
  - regex `T-(\d{3})` 抽 ticket 編號
  - `grep -liE "security.sensitive|schema.migration|auth|migration" tickets/T-XXX-*.md tickets/DONE/T-XXX-*.md` 任一命中就 chain
  - 找不到 ticket file 就維持原 default（只 chain `engineering-code-reviewer`）
- chain 是「**prompt Claude additionally** 跑某個 subagent」，不是 hook 自己 spawn——hook 沒能力 spawn subagent，只能在 deny message 裡寫 directive。
- 也考慮過 branch name regex 作為 trigger，但 branch name 容易拼錯；以 ticket file 為 source of truth 比較穩。
