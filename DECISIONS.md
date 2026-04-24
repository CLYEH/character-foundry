# Character Foundry — Key Decisions

> **Quick reference of load-bearing decisions。** 新 session 先讀這個，拿到 80% context。
> 詳細脈絡請進對應 planning 文件。

---

## 1. Identity & Framing

- **內部作業系統**，不是對外商品
- Horizontal 設計是**刻意**的（保留多種下游使用情境）
- AI 導覽員系統是下游使用者之一，不是產品定位
- 不做競爭分析、不找 wedge、不跟 HeyGen / VRoid 比較

---

## 2. Phase 1 Scope

**做：** image generation（Base + Alias）+ i2v motion（Preset 5 + Custom）+ ZIP 下載 + Team 共享與 Copy
**暫緩：** Lip sync、Image-to-3D（願景保留，Phase 1 不實作）
**不做：** Share link、多 team、MCP server 實作、行動版

---

## 3. Tech Stack

### Backend
- **Python** / FastAPI / SQLAlchemy 2.x（async）/ Alembic
- **arq + Redis** 任務佇列
- **PostgreSQL 15+** with pgvector、uuid-ossp、pgcrypto、pg_trgm

### Frontend
- **Vite + React 19** / React Router v7
- **shadcn/ui + Tailwind CSS 4**
- **Zustand + TanStack Query v5**
- React Hook Form + Zod
- `@microsoft/fetch-event-source` for SSE（JWT header）
- react-konva for Inpaint
- Lucide icons
- Vitest + Testing Library + Playwright
- pnpm + openapi-typescript

### AI
- **gpt-image-2** — Base 生成 (text2image)、Alias 編修 (image2image / inpaint)
- **Veo 3.1** — i2v（backend 內部把 parent 圖同時送 first+last frame，強化 identity preservation；使用者不感知）
- **gpt-5-mini** — Prompt Reconciler（中翻英 + 衝突解析）

### DevOps
- Ubuntu 22.04 LTS
- Docker + docker-compose v2
- 內網自架單機
- GitHub Actions（假設）
- Prometheus + Grafana + Loki

---

## 4. Identity Preservation — ALREADY VALIDATED

**gpt-image-2 的 identity preservation 經 inpaint / image2image 後仍可保持** ✅（使用者於 2026-04-23 確認）

→ Base → Alias pipeline 技術上成立。Data model 用 Character > Base + Aliases 結構無風險。

---

## 5. Data Model 核心概念

```
Character（角色）= 最上層容器
├─ Creation Session
│  └─ Checkpoints[]（迭代候選，可 fork 成新 Character）
├─ Base（最素的模樣，確立後 immutable）
│  └─ Motions[]（5 preset + N custom，on-click 生成）
└─ Aliases[]（平行變體，不階層）
   └─ Motions[]
```

**關鍵性質：**
- Base 確立後**不可變**
- Alias 永遠從 **Base** 生（不從其他 alias 衍生）
- Motion 綁在 Base **或** Alias 底下，不跨共享
- Copy 範圍 = Base + Aliases（不含 Motions, per B1）

---

## 6. Phase 1 基礎設施決策

| # | 項目 | 決定 |
|---|---|---|
| B1 | Copy 範圍 | Base + Aliases（不含 Motions）|
| B2 | 檔案儲存 | 本機 FS + abstract interface（之後可切 S3）|
| B3 | 部署 | 內網自架 server（單機）|
| B4 | 認證 | 簡單帳密 + JWT（access 15min, refresh 30d）|
| B5 | Team 模型 | 單一 team（schema 保留 team_id）|
| B6 | 成本控管 | 軟性 quota（UI 顯示不擋）|
| B7 | 語言 | UI 中文 + Prompt 英文（LLM 翻譯） |

---

## 7. Platform 原則

- **人機雙介面**：UI 給團隊成員 + API 給內部 agent（同一組 endpoint）
- **Skill 化能力**：所有核心能力 agent 都能呼叫（API + 未來 MCP）
- **Resource ID 穩定**：UUID v4 跨呼叫可組合
- **錯誤結構化**：`AgentError { code, message, problem, cause, fix, docs_url, retryable, request_id }`
- **非同步任務**：polling + SSE + webhook 三種通知方式
- **平台固定 constraints**：transparent bg / centered / facing camera / 注入所有 prompt

---

## 8. 語言策略（B7 細節）

- **UI**：繁體中文
- **使用者輸入**：中文
- **送模型 prompt**：英文
- **翻譯**：Prompt Reconciler LLM 同時做（翻譯 + 衝突解析 + 補述改寫）
- **Character / Alias / Motion 命名**：保留原文，不翻

---

## 9. 常見路徑速查

| 要找什麼 | 去哪 |
|---|---|
| Entity 關係 / DB schema | `planning/data/db-schema.md` |
| API 端點 / 錯誤格式 | `planning/backend/api-shape.md` |
| Task lifecycle / SSE | `planning/backend/task-queue.md` |
| AI client 設計 | `planning/backend/ai-integration.md` |
| Prompt Reconciler | `planning/backend/prompt-reconciler.md` |
| 頁面 flow | `planning/ux/user-flows.md` |
| Wireframes | `planning/ux/wireframes.md` |
| 元件對應 | `planning/frontend/component-map.md` |
| 前端 async (SSE / refresh / cancel) | `planning/frontend/async-patterns.md` |
| Env 變數 | `planning/devops/environment-variables.md` |
| Scheduled jobs | `planning/devops/operations.md` §1 |
| Docker compose 參考 | `planning/devops/deployment.md` §3 |

---

## 10. 關聯

- 當前進度：`STATUS.md`
- 所有單：`tickets/`
- 專案總覽：`CLAUDE.md`
- 詳細 planning：`planning/`
