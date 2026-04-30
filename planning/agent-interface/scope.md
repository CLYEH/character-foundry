# Agent Interface — Scope (M3.5 Draft)

> **Status:** Draft v0.1 · 2026-04-30
> **Owner:** Agent Interface Agent
> **Trigger:** 使用者於 2026-04-30 重申 agent-first / agent-native / agent-friendly 是 Character Foundry 的靈魂；MCP / OAuth 從 Phase 2 拉回 Phase 1 M3.5。

---

## 1. M3.5 milestone 定義

**完成條件：** 一個只讀過 OAuth 設定 + MCP tool schema 的外部 agent，能在不看 REST 文件的情況下走完 **M3 範圍內**所有功能（登入 → 建 character → 確立 base → 加 alias → 生 motion）。

> ⚠ 不含 ZIP 下載 / Copy / Usage——那是 M4 範圍，M3.5 ship 時還沒實作。M4 ticket 從 day 1 就會帶 scope decorator + MCP tool 條目，agent 自然拿到（per `STATUS.md` Sprint 4 規劃）。

這條件失敗 → M3.5 沒過。

## 2. M3.5 in scope

### 2.1 OAuth 2.1 auth flow
- **Authorization Code + PKCE** 給 human user（替換現有 JWT login）
- **Client Credentials** 給 agent / M2M（headless agent 取 token 不需要人）
- Scope 模型：人的 scope vs agent 的 scope 可能不同（見 `open-questions.md` Q5）
- Refresh token 整合既有 `refresh_token` model

### 2.2 MCP server
- 走 **streamable HTTP** transport（remote MCP 標配，stdio 不適合多客戶）
- Tool schema 從 OpenAPI 推導但**不是 1:1 wrap**——某些 endpoint 合併（例如 base 建立流程：開 session + 跑 checkpoint + 選 base 三個 endpoint，packaged 成單 tool `create_character`）
- Async task 走 MCP `progress` notification + 完成回 result（不要逼 agent polling REST）
- Error 結構化到 MCP error response，`fix` 欄位是 agent 可機器讀的 recovery action

### 2.3 Signed URL 與 storage
- 既有 signed URL 由 `STORAGE_SIGNED_URL_SECRET` JWT 派生 → 重新評估在 OAuth 下怎麼配對
- Agent download asset 時 token 模型要與 OAuth scope 對齊

## 3. M3.5 NOT in scope

- 跨 team agent 授權（保留 single team Phase 1 約束）
- Webhook 訂閱（既有 api-shape §3.4 已寫，Phase 2 才實作）
- Agent 之間互相 delegation（agent A 用 agent B 的權限）
- MCP server 對外發佈到 public registry（內部使用為主）

## 4. 與 Phase 1 既有設計的互動

| 元件 | 影響 | 處理方式 |
|---|---|---|
| `auth.py`（既有 JWT login） | OAuth 取代 JWT | 並存一段時間，JWT 漸進關閉 |
| `refresh_token` model | OAuth refresh 共用 | 加欄位區分 token 來源 |
| `STORAGE_SIGNED_URL_SECRET` | 與 OAuth 共生 | 見 `open-questions.md` Q6 |
| `AgentError` schema | MCP error mapping | 直接對應 |
| Task SSE | MCP progress notification | wrapper 即可 |
| `/v1/meta` `degraded_services` | MCP `tools/list` 上能看到 | 加欄位 |

## 5. 規劃啟動順序（**開任何 M3.5 ticket 前必讀**）

> ⚠ M3.5 不是「開 ticket → 直接做」可以動的——open-questions.md 有 9 條 + auth open-questions.md 有 8 條未決，多數彼此耦合。沒走完 plan phase 就開 ticket = 紙上空想 → 邊做邊改 → 大量返工。

### 5.1 前置條件
- **M3 必須 ship 完**（Sprint 3 全 ticket merge，M3 milestone 勾起來）。M3 前的 endpoint contract 還在動，MCP tool surface 沒對象可 wrap，OAuth scope 也沒實體可保護。

### 5.2 Plan phase 順序（**這條是 load-bearing**）

```
Step 1：agent-interface agent 拍板（先做，~1 週）
       └─ open-questions.md 全部 9 條：
            Q1 transport / Q2 顆粒度 / Q3 async / Q4 naming /
            Q5 agent vs human scope / Q6 signed URL（與 auth Q5 互鎖）/
            Q7 MCP exposure / Q8 versioning / Q9 endpoint blacklist
       output: MCP tool surface 雛形 + agent vs human 互動模型輪廓
                ↓
Step 2：auth agent 拍板（接續 step 1，~1 週）
       └─ ../auth/open-questions.md 全部 8 條：
            Q1 provider / Q2 agent grants / Q3 scope / Q4 JWT migration /
            Q5 signed URL（與 agent-interface Q6 互鎖，一起拍板）/
            Q6 refresh token / Q7 UI cutover（最終 UI 實作落 Step 4 frontend，
            策略決策仍在本步） / Q8 MCP-OAuth integration（與 step 1 Q1 transport 互鎖）
       Input dependency: step 1 的 tool surface（決定 scope 細粒度）
                ↓
Step 3：backend agent review（短，~0.5 週）
       └─ endpoint scope decorator + MCP tool 條目該怎麼長進每張 ticket
                ↓
Step 4：frontend + devops（並行，~0.5 + 0.5 週）
       ├─ frontend: authStore / login UI 改動範圍 + auth Q7 UI cutover 細節落地
       └─ devops: OAuth provider docker stack（若選自架）

> 17 條（agent-interface 9 + auth 8）每一條都有指定的 step owner；agent 不該因 bullet list 沒顯式列就以為「不必處理」。Step 1+2 各自的 open-questions.md 是 source of truth，本表只是 step ownership map。
```

**為什麼是這個順序：**
- step 1 之前動 step 2 → 不知道 tool 顆粒度，scope 沒辦法準確切（會切太粗或太細）
- step 2 之前動 step 3 → endpoint 還不知道要保護什麼，decorator 設計沒方向
- step 3 之前動 step 4 → frontend / devops 沒底層合約可實作

**怎麼啟動：** 開新 session 時對 Claude 說「請用 agent-interface agent 的視角，從 open-questions.md 開始，把 9 條全部拍板」。auth 同 pattern（8 條全部）。Step 3 是 backend agent 視角接到 agent-interface + auth 收斂後的 spec。

### 5.3 暫定時序（plan phase 收斂後）

| 階段 | 內容 | 估時 |
|---|---|---|
| Plan phase | §5.2 step 1-4 | ~3 週 |
| Sprint 3.5a | OAuth migration（auth.py + refresh_token + signed-URL）| 1.5 週 |
| Sprint 3.5b | MCP server 骨架 + 4 個 M3-範圍核心 tool（建 character / 加 alias / 生 motion / 列 character）| 2 週 |
| Sprint 3.5c | Agent E2E smoke：用一個外部 agent 跑完 §1 完成條件 | 0.5 週 |

實際時程要看 Phase 1 M3 收尾速度與你決定的 OAuth provider。

## 6. 關聯文件

- `open-questions.md` — 需要決策的事項
- `mcp-surface.md`（待開）— tool schema 詳列
- `../auth/` — OAuth 那一塊
- `../backend/api-shape.md` — REST 合約（agent surface 的起點）
- `../product/functional-scope.md` §4.6 F-50, F-51
