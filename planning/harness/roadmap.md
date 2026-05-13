# Harness — Roadmap

> **Status:** Draft v0.1 · 2026-05-11
> **Owner:** Harness Agent
> **Related:** `scope.md`（gap 分析來源）

---

## 0. 排序原則

三個 ranking 維度：

1. **Cost**（小時計）
2. **跟 M3.5 OAuth + MCP 工作的時序耦合度**——耦合越強越要前移（事後做要付遷移成本）
3. **Value 是否隨時間累積**——累積型的越早裝越早收益

三 tier：

- **A** = M3.5 動工前該裝好（T-052 開工前一週）
- **B** = M3.5 ship 完第一個 sprint 內
- **C** = 架構級，有空再做

---

## 1. A-tier（M3.5 動工前）

> 預期總工時：~1.5 天

### A1. 真 provider contract replay（manual-only since T-066）

**做什麼：**
- 加 pytest marker `@pytest.mark.real_provider`
- 對 gpt-image-2 / gpt-5-mini / Veo 3.1 各一個最便宜的真 call
- **只斷言 response shape**，不斷言內容
- GitHub Actions `workflow_dispatch`，**不掛 cron**（T-066 變更：原 nightly UTC 00:00 cron 對單人專案 ~$10/月不划算；trigger 模型 push-based → pull-based）
- 失敗時自動開 ticket（label `provider-drift`）
- 觸發時機：動到 `app/ai/*` client 或 `_parse_*` 函式時，PR open 後手動 `gh workflow run provider-contract.yml`

**為什麼前移：**
- T-042 / T-045 / T-051 都是同模式（stub 跟真 provider 漂移）連踩三次
- M3.5 之後 MCP tool 會 1:1 wrap 這幾條，shape 漂移會同時打 REST + MCP 兩面

**預估工時：** 4-6h（含 GH Actions workflow + secret 設定）

**Owner trigger：** T-058 land 2026-05-12；T-066 改 manual-only 2026-05-13

---

### A2. Architecture fitness — layering test

**做什麼：**
- `api/tests/arch/test_layering.py`，用 `import-linter` 或 30-50 行 `ast` walk
- 斷言：
  - `app/api/routes/*` 不可直接 import `app/models/*`（要走 `repositories/` / `schemas/`；本 repo ORM 在 `app/models/`，`app/db/` 只有 `base.py` + `session.py` infra）
  - `app/ai/*` 不可 import `app/api/*`
  - 未來 `app/mcp/*` 與 `app/auth/*` 必須共用同一個 scope source（不可各自硬編 scope 字串）
- 配合 mypy CI 一起跑

**為什麼前移：**
- M3.5 加兩個新 layer（OAuth middleware、MCP server）
- 沒 arch test → 兩個新 layer 之間的 dependency 方向會悄悄歪
- ticket `_TEMPLATE.md` 已經有 "OAuth scope required" guide，但**沒有對應 sensor**——這條補上

**預估工時：** 3h

**Owner trigger：** 開 T-059

---

### A3. Coverage + mutation gate（auth + errors + circuit）

**做什麼：**
- `pyproject.toml` pytest config：`--cov-fail-under=<baseline>`（先用 T-060 第一次跑出來的 baseline，不硬鎖 80%——硬鎖會在 baseline 低於 80% 時立刻紅 CI；ticket 採後續手動往上爬的策略）
- CI workflow `pr.yml` backend job：失敗就 red
- `mutmut` on `app/core/errors.py` + `app/ai/circuit.py` + `app/auth/*`
  - kill rate 門檻同樣先設 baseline - 5%，三個月後手動往上爬
  - 跑在 nightly（不在 PR CI，太慢）

**為什麼前移：**
- CONTRIBUTING §4.1 Phase 1 solo exception 把 security-sensitive PR 的第二個人類 reviewer 換成 Codex `+1`
- T-054 dual-stack middleware 是 security-sensitive；mutation 結果是「自動的第二雙眼睛」
- OAuth 換血前的保險

**預估工時：** 3h（含 mutmut baseline 第一次跑時間）

**Owner trigger：** 開 T-060

---

### A4. Secret scan + SAST

**做什麼：**
- `gitleaks` 進 pre-commit + CI（兩處都跑）
- `bandit -r app/` 進 backend CI
- `semgrep --config p/owasp-top-ten` 進 backend CI
- 三者 baseline 第一次跑可能 noisy，先 false-positive triage 一輪

**為什麼前移（關鍵）：**
- **必須在 T-053 之前**——T-053 = "Authentik client 註冊"，client_secret 就會進 `.env` / 進 repo 變數
- Authentik OAuth keypair 一旦 commit 到歷史，後續清歷史比一開始就攔住貴 10 倍

**預估工時：** 4h（含 baseline triage）

**Owner trigger：** 開 T-061，**鎖在 T-053 之前**

---

### A5（原 B7 升 A）. Subagent stack 擴充

**做什麼：**
- Fork `agency-agents` 的 `security-engineer.md` + `db-optimizer.md` 進 `.claude/agents/`
- 改 `.claude/hooks/pre-push-review.sh`：對 security-sensitive / schema-migration ticket 自動 chain（不是默認對所有 PR）
- 判斷依據：branch name regex 或 ticket file path（`T-054` / `T-055` / `T-057` 在 OAuth 系列）

**為什麼前移（從 B 升 A）：**
- T-052 ~ T-057 **全部** security-sensitive
- T-055 是 schema migration（refresh_token 加欄位）
- 原本歸 B 是因為以為 M3.5 還遠，重審時意識到下一張 ticket 就要用

**預估工時：** 2h

**Owner trigger：** 開 T-062

---

### A6（原 B8 升 A）. `CF_SKIP_REVIEW=1` audit log

**做什麼：**
- 在 `.claude/hooks/pre-push-review.sh` + `.githooks/pre-push` 兩個 hook 的 bypass 分支加 ~5 行
- bypass 觸發時 append 到 `.harness/skip-review.log`（gitignore 該檔案，本機累積即可）
- 欄位：timestamp、branch、commit range、`CF_SKIP_REVIEW_REASON` env（選填）
- 季度 retro 時看 bypass 率趨勢

**為什麼前移：**
- 5 行 shell，cost 接近零
- value 來自時間累積——越早裝越快有 baseline
- 不裝就觀察不到「我們已經習慣性 bypass review」這條 drift

**預估工時：** 30min

**Owner trigger：** 開 T-063（可跟 A5 合單）

---

## 2. B-tier（M3.5 ship 完第一個 sprint）

### B5（原 B5）. Prompt assembly snapshot

**做什麼：**
- `pytest` + `syrupy` 或 `inline-snapshot`
- 固定 N 組輸入（base / alias / motion mode × 1-2 種 menu 組合）→ assert final prompt fixture
- 變更 `reconciler_client.py` / `menu_fragments.py` / `platform_constraints.yaml` 任何一處 → snapshot diff 必須出現在 PR diff 上

**條件性前移**：
- 如果接下來兩 sprint 會碰 reconciler / `platform_constraints.yaml` / `menu_fragments.py` → 現在做
- 如果不會碰 → 維持 B（M3.5 後再做）

**理由不前移到 A**：
- 不擋 M3.5 OAuth / MCP 工作
- T-051（Veo RAI filter）改的是 error mapping，不是 prompt assembly

**預估工時：** 1-2h

---

### B6. LLM-as-judge for AI 輸出（**不前移**）

**做什麼：**
- nightly 跑 fixed seed → 真 provider → 拿輸出餵 Claude / gpt-5 當 judge 評分
- Rubric：face geometry preserved? framing rule honored? transparent bg? identity preservation?
- score trend storage（簡單 sqlite 或 csv）
- 分數滑超過閾值自動開 ticket

**為什麼不前移**：
- 比 A1 重——需要 rubric 設計 + threshold tuning 兩個未知數
- M3.5 sprint 中段一旦 false positive 觸發 "圖看起來怪了" alert → 分散注意力
- 等 A1 有 baseline、跑兩週確認 shape 沒亂變，再加 quality judge

**預估工時：** 1 天

---

## 3. C-tier（架構級）

### C9. Dogfood 對外 MCP server 給 coding agent 自己

**做什麼：**
- M3.5 `app/mcp/` server 完工後，多 export read-only resources
- 候選：`planning/<agent>/*.md`、`tickets/**`、`STATUS.md`、`DECISIONS.md` 切片
- coding agent 不必 `find` / `grep` 整個 repo，可直接 `mcp_query("backend/api-shape.md §4")`

**為什麼放 C：**
- 必須等 M3.5 server ship 完
- 現階段檔案 < 200 個，`Grep` / `Read` 還夠用
- 真正的 value 在 cross-session 導航（看 `tickets/DONE/` 過往 push-back 先例）

---

### C10. LLM-optimized custom lint rule

**做什麼：**
- 自訂 ruff rule（或 pre-commit script）：`app/api/routes/*` 禁用 `raise HTTPException(...)`
- error message 寫成 LLM-friendly：「Use one of `AgentErrorException` factories from `app/core/errors.py` — e.g. `auth_invalid_credentials()` for 401 / `validation_error()` for 422」
- 同類擴充：禁直接 `await db.execute(text(...))` 在 route 層、禁從 `app/api/` import `app/models/`

**為什麼放 C：**
- 手刻 ruff plugin 或 ast hook 工時不小（半天起跳）
- A2 layering test 已經擋掉一部分結構性錯誤
- 等 OAuth + MCP 落地之後，pattern 才會穩定到值得寫 rule

---

### C11. Scaffold codemod

**做什麼：**
- `scripts/new_endpoint.py <name> <scope>` → 生 route + repo + schema + AgentError 預設 + scope decorator + e2e spec 殼
- `scripts/new_ai_client.py <provider>` → 生 client + stub + factory entry + circuit wrap + contract test 殼
- `scripts/new_mcp_tool.py <tool_name>`（M3.5 後）→ 生 tool registry entry + scope mapping + integration test

**為什麼放 C：**
- 現階段 endpoint / AI client / MCP tool 的 boilerplate 還在演化
- pattern 沒穩定就寫 codemod，會被 codemod 自己鎖死設計
- 建議 M3.5 ship 完 + Sprint 4 中段（pattern 穩定）再做

---

### C12. Bundle budget + perf SLO

**做什麼：**
- `vite build` 加 size budget（超過閾值 CI red）
- Playwright `e2e/perf.spec.ts`：login → dashboard render < N ms、character create flow < M ms
- 整合到 PR CI 的 e2e step

**為什麼放 C：**
- Phase 1 還沒到效能受苦
- 早裝會在沒效能問題的時候製造 false alarm

---

## 4. 時程建議

```
T-058 (A1 real-provider replay, manual-only post-T-066) ─┐
T-059 (A2 layering arch test)             │  M3.5 動工前一週
T-060 (A3 cov gate + mutmut)              │  ←
T-062 (A5 subagent stack)                 │
T-063 (A6 skip-review log, 併入 A5)       │
                                          │
T-061 (A4 secret scan + SAST) ────────────┤  ★ 必須在 T-053 之前
                                          ┘
─────────────── T-052 ~ T-057 OAuth 系列 ───────────────

M3.5 ship 後第一個 sprint：
T-064? (B5 prompt snapshot, 條件性)
T-065? (B6 LLM-as-judge)

Sprint 4 中段：
T-066? (C11 scaffold codemod)
T-067? (C9 MCP dogfood)
T-068? (C12 perf budget)
T-069? (C10 custom lint rule)
```

T-058 ~ T-063 編號為**預定**，實際開單時依當下 `STATUS.md` 最大編號 + 1。

---

## 5. 不做（明文 out-of-scope）

- **自己刻 lint rule engine**（用 ruff / eslint 的 plugin API 即可）
- **自己刻 MCP server for 給 coding agent 用**（C9 直接 dogfood 對外那一份）
- **GitHub Actions cron 換成 self-hosted runner**（cost 還沒到痛點）
- **強制 100% coverage**（80% 已是合理門檻；80 → 100 的 marginal 通常是 boilerplate test，會 incentivize agent 寫無意義 test）

---

## 6. Open questions（之後可開 `open-questions.md`）

- A1 真 provider replay 的 secret 怎麼放？GH Actions secrets vs. dedicated test account？
- A3 mutation kill-rate 門檻 80% 是合理數字嗎？要不要先跑一輪看 baseline 再決定？
- A5 subagent 的 trigger 是 branch name regex 還是讀 ticket file 取 type？
- C9 MCP dogfood 時，planning 文件要不要先做 chunking + embedding，還是純檔名 + 段落號 lookup 就夠？
