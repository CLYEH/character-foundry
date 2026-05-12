# T-061: Secret scan + SAST baseline

**Status:** TODO
**Sprint:** 3.5-pre（Harness pre-flight）
**Est:** S（4h，含 baseline triage）
**Depends on:** none
**Related:** T-053（**hard sequencing**：本單必須在 T-053 之前 land，否則 Authentik `client_secret` 一進 repo 歷史就晚了）

---

## Scope

加 `gitleaks` / `bandit` / `semgrep` 三層 scan 進 pre-commit + CI，建立 baseline 並 triage 既有 false positive。

**In scope:**
- `gitleaks` 進 pre-commit + PR CI（兩處都跑，pre-commit 是 fast feedback，CI 是守門）
- `bandit -r app/` 進 PR CI backend job
- `semgrep --config p/owasp-top-ten` 進 PR CI backend job
- 三者 baseline 第一次跑必然 noisy → false positive triage，寫入 ignore list 並標註原因
- gitleaks 配置：偵測 `.env` accidental commit、API key pattern（OpenAI / Veo / Anthropic / Authentik client_secret 預先加 pattern）

**Not in scope**（保留給其他單）：
- Frontend SAST（eslint security plugin 之類，B/C-tier 之後）
- Dependency scanning（dependabot 已啟用？若無另開單）
- Runtime DAST / penetration test（M3.5 完工後 `/cso` skill 可跑）

---

## Planning refs（開工前必讀）

- `planning/harness/roadmap.md` §1 A4 — **hard sequencing rationale**（為什麼必須在 T-053 之前）
- `tickets/T-053-authentik-idp-and-clients.md` — 確認 client_secret 進入路徑

---

## Acceptance criteria

- [ ] `gitleaks` 加進 `.pre-commit-config.yaml` + `.github/workflows/pr.yml`
- [ ] `bandit` + `semgrep` 加進 backend CI job
- [ ] 三者各自跑過 baseline，false positive 列入 ignore list（gitleaks `.gitleaksignore` / bandit `# nosec` 註解附原因 / semgrep `.semgrepignore`），**禁止無說明 ignore**
- [ ] `.env.example` **仍在 gitleaks 掃描範圍**（不整檔 ignore；真有 placeholder 觸發 false positive 用 `gitleaks.toml` `allowlist.regexes` 對應 placeholder pattern 開白，**不可加進 `.gitleaksignore` 整檔豁免**——那會放掉一個 tracked file 的 secret coverage）
- [ ] 故意送一個 fake secret（dummy `sk-test123456` 字串）進 PR 驗證 gitleaks 真的攔得住
- [ ] STATUS.md 更新後 T-053 ticket 加 `Depends on: T-061`
- [ ] 三者執行時間總和不超過 PR CI 既有時間的 +20%

---

## Files expected to touch

- `.pre-commit-config.yaml` (edit) — 加 gitleaks
- `.github/workflows/pr.yml` (edit) — backend job 加 bandit + semgrep + gitleaks step
- `.gitleaksignore` (new) — baseline false positive
- `.semgrepignore` (new) — baseline false positive
- `api/pyproject.toml` (edit) — bandit 設定 inline 或獨立 `.bandit` 檔
- `tickets/T-053-authentik-idp-and-clients.md` (edit) — 加 `Depends on: T-061`
- `STATUS.md` (edit) — 更新 dependency 圖

---

## OAuth scope required

n/a

---

## MCP tool delta

n/a

---

## Notes

- **Sequencing 是這條 ticket 的最大價值**：T-053 一旦 land，Authentik `client_secret` 進 `.env`、進可能漏進歷史。事後清歷史（`git filter-repo`）比一開始就攔住貴 10 倍以上。**本單未 merge，T-053 不能開工**。
- baseline triage 工時可能不到 4h——但 noise 程度依 repo 狀態而定，不要過度樂觀估時。
- gitleaks pattern 預先加 Authentik `client_secret` 字串樣式（看 Authentik docs 對應 prefix）；OpenAI / Anthropic / Veo 的 key prefix 內建 detector 就能抓。
- semgrep p/owasp-top-ten 對 FastAPI 的 false positive 比較多——`sql_injection` 在 SQLAlchemy ORM 路徑常誤報；triage 時記得標明「ORM 不適用」。
- bandit `B101 assert_used` 在 tests/ 一定爆——`pyproject.toml` 設 `exclude_dirs = ["tests"]`。
