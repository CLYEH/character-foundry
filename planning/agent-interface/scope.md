# Agent Interface — Scope (M3.5 Draft)

> **Status:** Draft v0.1 · 2026-04-30
> **Owner:** Agent Interface Agent
> **Trigger:** 使用者於 2026-04-30 重申 agent-first / agent-native / agent-friendly 是 Character Foundry 的靈魂；MCP / OAuth 從 Phase 2 拉回 Phase 1 M3.5。

---

## 1. M3.5 milestone 定義

**完成條件：** 一個只讀過 OAuth 設定 + MCP tool schema 的外部 agent，能在不看 REST 文件的情況下走完「登入 → 建 character → 確立 base → 加 alias → 生 motion → 下載 ZIP」全流程。

這條件失敗 → M3.5 沒過。

## 2. M3.5 in scope

### 2.1 OAuth 2.1 auth flow
- **Authorization Code + PKCE** 給 human user（替換現有 JWT login）
- **Client Credentials** 給 agent / M2M（headless agent 取 token 不需要人）
- Scope 模型：人的 scope vs agent 的 scope 可能不同（見 §4 open question）
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
| `STORAGE_SIGNED_URL_SECRET` | 與 OAuth 共生 | 見 §4 open question |
| `AgentError` schema | MCP error mapping | 直接對應 |
| Task SSE | MCP progress notification | wrapper 即可 |
| `/v1/meta` `degraded_services` | MCP `tools/list` 上能看到 | 加欄位 |

## 5. 暫定時序（M3 後接續）

| 階段 | 內容 | 估時 |
|---|---|---|
| Sprint 3.5a | OAuth provider 抉擇 + spec 確認；MCP transport 抉擇 | 1 週 plan |
| Sprint 3.5b | OAuth migration（auth.py + refresh_token + signed-URL）| 1.5 週 |
| Sprint 3.5c | MCP server 骨架 + 5 個核心 tool（建 character / 加 alias / 生 motion / 下載 / 列 character）| 2 週 |
| Sprint 3.5d | Agent E2E smoke：用一個外部 agent 跑完 §1 完成條件 | 0.5 週 |

實際時程要看 Phase 1 M3 收尾速度與你決定的 OAuth provider。

## 6. 關聯文件

- `open-questions.md` — 需要決策的事項
- `mcp-surface.md`（待開）— tool schema 詳列
- `../auth/` — OAuth 那一塊
- `../backend/api-shape.md` — REST 合約（agent surface 的起點）
- `../product/functional-scope.md` §4.6 F-50, F-51
