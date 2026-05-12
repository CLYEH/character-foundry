# T-063: `CF_SKIP_REVIEW=1` audit log

**Status:** TODO
**Sprint:** 3.5-pre（Harness pre-flight）
**Est:** XS（30min）
**Depends on:** none
**Related:** T-062（subagent stack，本單是 review gate 的 observability 補位）

---

## Scope

兩個 pre-push hook（Claude-side + terminal-side）的 `CF_SKIP_REVIEW=1` bypass 分支加 append-only log，累積 bypass 使用紀錄供季度 retro 觀察 drift。

**In scope:**
- `.claude/hooks/pre-push-review.sh` bypass 分支加 ~5 行 append 到 `.harness/skip-review.log`
- `.githooks/pre-push` 同步加（mirror 邏輯）
- log 欄位：ISO8601 timestamp / branch / commit range / `CF_SKIP_REVIEW_REASON` env（選填）
- `.harness/skip-review.log` 加進 `.gitignore`（本機累積，不 sync）
- `.harness/` 目錄首次建立要附 `.gitkeep` 或 README

**Not in scope**（保留給其他單）：
- Bypass rate 自動 alert（超過閾值的話 → 跨 session retro 工具範圍）
- 把 log sync 到任何外部 storage（本機就夠用）
- 改 bypass 本身的條件（CONTRIBUTING §7.1 既有規則維持）

---

## Planning refs（開工前必讀）

- `planning/harness/roadmap.md` §1 A6 — value-from-time-accumulation rationale
- `CONTRIBUTING.md` §7.1 + memory `feedback_cf_skip_review_is_sanctioned.md` — bypass 既有定位

---

## Acceptance criteria

- [ ] 兩個 hook 在 `CF_SKIP_REVIEW=1` 觸發時 append 一筆紀錄
- [ ] log 格式可 grep / 統計（用 `\t` 分隔欄位或 JSONL 任一）
- [ ] `CF_SKIP_REVIEW_REASON=foo CF_SKIP_REVIEW=1 git push` 把 reason 寫入紀錄
- [ ] `.harness/skip-review.log` 已加 `.gitignore`，`.harness/` 自身被 commit（含 README 或 .gitkeep）
- [ ] 手動驗證：bypass 兩次 + cat 出 log 看到兩筆

---

## Files expected to touch

- `.claude/hooks/pre-push-review.sh` (edit) — bypass 分支加 append log
- `.githooks/pre-push` (edit) — 同步邏輯
- `.gitignore` (edit) — 加 `.harness/skip-review.log`
- `.harness/README.md` (new) — 一段說明此目錄用途 + log schema + retro 怎麼讀

---

## OAuth scope required

n/a

---

## MCP tool delta

n/a

---

## Notes

- 5 行的 cost，但 value 完全來自時間累積——越早裝越快有 baseline。三個月後第一次 retro，看 bypass 率與 reason 分布，就能判斷 review gate 是否在被走形式。
- log schema 建議 JSONL：每行 `{"ts":"...","branch":"...","range":"...","reason":"..."}`，未來 `jq` 統計方便。
- **不要 sync log 進 repo**——個人開發節奏不該成為 PR diff 的一部分。本機累積，retro 時手動匯總。
- A6 在 roadmap 跟 A5（T-062）同時動，可考慮合單；分開比較清楚但要看當下開工狀態。
