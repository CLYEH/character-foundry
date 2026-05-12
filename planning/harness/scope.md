# Harness — Scope & Inventory

> **Status:** Draft v0.1 · 2026-05-11
> **Owner:** Harness Agent
> **Trigger:** 使用者於 2026-05-11 要求對照 Martin Fowler "Harness Engineering for Coding Agents" 健檢本專案 harness。
> **Reference:** https://martinfowler.com/articles/harness-engineering.html

---

## 1. 詞彙與架構

按 Fowler 的定義：

```
Agent = Model + Harness
```

Harness 包含**所有非 model 的東西**，分兩條主軸：

| 主軸 | 中文 | 內容 |
|---|---|---|
| **Feedforward controls (guides)** | 前饋（事前引導） | inferential guides（文件 / spec）、computational guides（codemod / codegen / LSP）、context provision（MCP）|
| **Feedback controls (sensors)** | 反饋（事後感測） | computational sensors（lint / type / test / cov / mutation / arch test）、inferential sensors（AI reviewer / LLM-as-judge）|

另外三個維度：

- **三種 harness 主題**：maintainability harness / architecture fitness harness / behaviour harness
- **lifecycle distribution**：pre-commit / pre-integration / post-integration / continuous monitoring
- **steering loop**：從觀察到的 agent 失敗模式回灌 harness 設計

---

## 2. 現況盤點（2026-05-11 snapshot）

對照 Fowler 框架逐維度盤點。

### 2.1 Inferential guides（強項）

| 載體 | 用途 |
|---|---|
| `CLAUDE.md`（+ `AGENTS.md` symlink）| 專案定位 / agent 必讀順序 / branch 規則 / auto-loop merge gate |
| `DECISIONS.md` | 80% context 快查 |
| `CONTRIBUTING.md` | git / PR / review 規則 |
| `CONTRIBUTING.md` §4.5 push-back doctrine | **明文反對 100% Codex 採納**——這是 steering loop 校正的書面化 |
| `planning/<agent>/CLAUDE.md` × 8 | 8 個 sub-agent 視角（product / ux / frontend / backend / data / devops / agent-interface / auth）|
| `tickets/_TEMPLATE.md` | 已含 OAuth scope + MCP tool delta 欄位 |
| `tickets/PARALLEL_WORKFLOW.md` | worktree pre-flight / 上游檢查 / conflict SOP |
| `.github/pull_request_template.md` | E2E coverage gate（T-049 後加）+ Codex review 回應勾選 |

**這層在 Fowler case study 等級**，主要因為使用者本身對 agent 友善設計有強意識。

### 2.2 Computational guides（中等）

| 載體 | 覆蓋 |
|---|---|
| `.pre-commit-config.yaml` | ruff / ruff-format / mypy / eslint / prettier |
| `web/package.json` scripts | `typegen`（openapi-typescript 從 backend OpenAPI 推 frontend type）|
| `api/platform_constraints.yaml` v1.2 | 含 `_logic_version` + `constraint_version` cache invalidation |
| 無 | **沒有 scaffold codemod**（新 endpoint / 新 AI client / 新 MCP tool 全靠手刻 boilerplate）|
| 無 | **沒有 PreToolUse 即時 type/lint feedback**（mypy / tsc 只在 commit 端跑）|

### 2.3 Computational sensors（不均）

| 已有 | 缺 |
|---|---|
| ruff（基本 rule set: E / W / F / I / B / UP）| 沒開 S / N / DTZ / SIM / TRY / PLR / C901（複雜度）|
| mypy --strict（exclude alembic）| —— |
| pytest + `--cov`（**未鎖 `--cov-fail-under`**）| 沒鎖 coverage 門檻 |
| ESLint + Prettier + tsc | —— |
| Vitest + Playwright e2e on docker compose stack | —— |
| AI stub mode + circuit breaker（`app/ai/circuit.py`）| —— |
| Structured `AgentError {code, problem, cause, fix, retryable, request_id}` | —— |
| `tests/ai/test_gpt_image_2_contract.py`（outgoing-body contract）| 沒有 **真 provider** 的 contract replay |
| 無 | dead code（vulture / knip / ts-prune）|
| 無 | duplication（jscpd）|
| 無 | mutation（mutmut / stryker）|
| 無 | architecture / layering test |
| 無 | secret scan（gitleaks）|
| 無 | SAST（bandit / semgrep）|
| 無 | bundle size budget |

### 2.4 Inferential sensors（薄）

| 已有 | 缺 |
|---|---|
| Codex App auto-review on PR + `+1` reaction merge gate | —— |
| `.claude/agents/engineering-code-reviewer.md` subagent | 只有一個——沒有 `security-engineer` / `db-optimizer` / `llm-output-judge` |
| Auto-loop merge gate（含 `mergeable: CONFLICTING` 預警，T-033 lesson）| —— |
| pre-push hook 雙軌（`.claude/hooks/pre-push-review.sh` + `.githooks/pre-push`，`CF_SKIP_REVIEW=1` bypass）| **bypass 沒有 audit log** |

### 2.5 三種主題 harness 的覆蓋

| 主題 | 覆蓋度 | 說明 |
|---|---|---|
| **Maintainability** | 中 | linter / format / type / test 都有，但門檻（coverage / 複雜度）沒鎖；無 dead-code / duplication / mutation |
| **Architecture fitness** | **零** | 沒 layering test、沒 perf SLO、沒 observability convention check。Prometheus / Grafana / Loki 在 DECISIONS §3 寫了但沒 wire 進 coding loop |
| **Behaviour** | 中 | unit + e2e + stub mode + circuit breaker + 一條 outgoing-body contract test；無 prompt assembly snapshot、無真 provider replay、無 LLM-as-judge |

### 2.6 Lifecycle distribution

| 階段 | 已部署 sensor | 空洞 |
|---|---|---|
| **pre-commit** | ruff / mypy / eslint / prettier | —— |
| **pre-push** | engineering-code-reviewer subagent（可 `CF_SKIP_REVIEW=1` bypass）| 無 bypass log |
| **PR CI** | backend lint+type+test、frontend lint+type+test、e2e on docker compose | 無 coverage gate、無 SAST、無 secret scan |
| **PR 開後** | Codex App auto-review + auto-loop merge gate | —— |
| **post-integration / nightly** | **幾乎空的** | 無 nightly real-provider replay、無 scheduled CSO audit、無 LLM-as-judge、無 SLO 回灌 |

### 2.7 Harnessability（強項）

> Fowler: 「ambient affordances — designing structural properties that make systems legible and tractable to agents」

| 設計選擇 | Harnessability 益處 |
|---|---|
| `AIClient` Protocol（`app/ai/base.py`）+ stub / real 雙實作 | agent 可以單測，CI 不打真 API |
| `StorageBackend` 抽象（LocalFilesystemBackend）+ 之後可切 S3 | agent 改檔不必碰 infra |
| UUID v4 stable IDs 跨呼叫可組合 | agent 不需先解析回應拿 ID 再呼下一招 |
| 結構化 `AgentError {code, problem, cause, fix, retryable, request_id}` | agent 可機器讀錯誤、自我修正 |
| Three async surfaces（polling / SSE / webhook）| agent 挑自己擅長的訂閱模型 |
| `platform_constraints.yaml` versioned variety reducer | Ashby's Law：把 prompt 組裝的 state space 降維 |

### 2.8 Steering loop 範例（強項）

書面化的校正紀錄：

| 範例 | 校正內容 | 出處 |
|---|---|---|
| **CONTRIBUTING §4.5** | 觀察到反射性 100% Codex 採納 → 寫成 push-back doctrine | `CONTRIBUTING.md` §4.5 |
| **T-049** | 觀察到 PR 反覆忘 e2e → 強制 PR template e2e gate | `tickets/DONE/T-049-*.md` + PR template |
| **CLAUDE.md `mergeable: CONFLICTING` 段落** | T-033 worktree 踩過 CI 神秘停跑 → 把判讀寫進 auto-loop spec | `CLAUDE.md` 內嵌 |
| **`_TEMPLATE.md` OAuth scope / MCP tool delta 欄位** | 為了 M3.5 不要事後補 → 從 ticket day 1 就帶 | `tickets/_TEMPLATE.md` |

**但缺**：沒有系統化的 failure-pattern ledger。**T-042 / T-045 / T-051 是同一條 pattern（真 provider 回應 shape 與 stub 漂移）連踩三次**，還沒長出對應的 sensor（見 roadmap A1）。

---

## 3. 主要 gap 摘要

按嚴重度排序：

1. **Architecture fitness 幾乎零**——M3.5 加 OAuth middleware + MCP server 兩個新 layer，沒 arch test 會偷偷穿層。
2. **真 provider drift sensor 缺**——T-042 / T-045 / T-051 同模式三次。
3. **OAuth 落地前該有的 sensor 還沒裝**——secret scan / SAST / mutation on auth。
4. **Post-integration 那層幾乎空的**——nightly job 沒有 cron 設好。
5. **Inferential sensor stack 只有一個 subagent**——security-engineer / db-optimizer 對 M3.5 sprint 馬上要用。
6. **CF_SKIP_REVIEW=1 沒 audit log**——觀察不到 bypass-rate 趨勢。
7. **沒 scaffold codemod**——新 endpoint / 新 AI client / 新 MCP tool 都靠手刻。
8. **沒 LLM-optimized linter message**——LLM 寫 FastAPI 預設 `raise HTTPException`，但 repo 用 `AgentErrorException` envelope。
9. **沒 prompt assembly snapshot**——`platform_constraints.yaml × menu_fragments × reconciler` fan-out 沒 fixture lock。
10. **MCP server 還沒 dogfood 給 coding agent 自己**（M3.5 後才可能）。

具體補強排序見 `roadmap.md`。

---

## 4. 不在這個 scope（給其他 agent 的）

- 對外 agent surface（MCP tool 顆粒度、agent-native error semantics）→ `agent-interface` agent
- OAuth flow / scope 模型本身 → `auth` agent
- Observability infra（Prometheus / Grafana / Loki 部署）→ `devops` agent
- API endpoint 規格 → `backend` agent

Harness agent 關心的是「**這些東西怎麼被 enforce 進 coding agent 的工作流**」，不是規格本身。
