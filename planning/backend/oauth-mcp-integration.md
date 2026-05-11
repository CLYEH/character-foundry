# OAuth + MCP Integration（backend agent，M3.5）

> **Owner:** Backend Agent
> **Created:** 2026-05-07
> **Status:** Locked（M3.5 plan phase Step 3）
> **Upstream decisions:** `planning/agent-interface/open-questions.md` Round 1/2/3 · `planning/auth/open-questions.md` 決策紀錄

---

## 1. 目的

把 M3.5 plan phase Step 3 的三個決定變成可施工形式：

1. 每張新 backend ticket 怎麼宣告 endpoint 的 OAuth scope
2. 每張 backend ticket 怎麼宣告對 MCP tool registry 的影響
3. Endpoint 和 MCP tool 在 code 上的標準長相

---

## 2. Endpoint scope 強制機制

### 2.1 規則

每個受保護的 REST endpoint 都必須透過 FastAPI `Depends(require_scope(...))` 宣告所需 scope。沒寫 → 預設 deny。

### 2.2 標準 pattern

```python
from app.auth.scopes import require_scope

@router.post("/characters")
async def create_character(
    payload: CreateCharacterIn,
    _: None = Depends(require_scope("character:write")),
):
    ...
```

### 2.3 多 scope 場景

- **AND**（兩個 scope 都要）：`Depends(require_scope("character:write", "task:read"))`
- **OR**（任一即可）：用兩個 endpoint 拆，不在 decorator 層處理

### 2.4 為什麼選 `Depends` 而非 custom decorator

- Native FastAPI primitive，OpenAPI auto-docs 看得到（agent / human 都能讀）
- Testing 用 `app.dependency_overrides[require_scope]` 直接 stub，不必動 decorator
- 與其他 Depends（DB session、current user）組合自然

反方案 custom `@requires_scope` decorator 缺點：要自己解 OpenAPI integration、testing fixture 多。

---

## 3. MCP tool registry

### 3.1 規則

所有 MCP tool 集中在 `app/mcp/tools/` 下，每個 namespace 一個檔（例：`character.py` / `motion.py` / `task.py`）。

REST endpoint 不知道自己屬於哪個 tool——關係是 tool → endpoints（單向）。

### 3.2 標準 schema

```python
# app/mcp/tools/character.py
from app.mcp.registry import MCPTool

character_create = MCPTool(
    name="character.create",
    description="Create a new character with prompt or reference image, including base lock.",
    scopes=["character:write", "task:read"],
    bundles=[
        "POST /v1/characters",
        "POST /v1/characters/{id}/sessions",
        "POST /v1/sessions/{id}/checkpoints",
        "POST /v1/sessions/{id}/select-base",
    ],
    input_schema=...,   # pydantic
    output_schema=...,
)

character_list = MCPTool(
    name="character.list",
    scopes=["character:read"],
    bundles=["GET /v1/characters"],
    ...
)
```

### 3.3 Packaging 與 1:1 wrap 的選擇

根據 agent-interface Round 1 Q2：
- **核心流程 tool**（建立、改寫、生成）→ packaging（一個 tool 包多個 endpoint）
- **CRUD tool**（list / get / rename / delete）→ 1:1 wrap

判斷規則：若 agent 為了完成「一件事」需要連呼 ≥2 個 endpoint，packaging。

### 3.4 Scope 推導

Tool 的 `scopes` field 必須是 `bundles` 內所有 endpoint scope 的 union——CI 會驗證這個不一致。

---

## 4. Ticket 模板新欄位

`tickets/_TEMPLATE.md` 已加 2 個 section：

- **OAuth scope required** — 後端 endpoint ticket 必填；frontend / docs / infra ticket 寫 `n/a`
- **MCP tool delta** — 新工具 / 修工具 / `n/a`

每張新 backend ticket 從 M3.5 起遵守。

---

## 5. CI / lint 護欄（M3.5 ship 前）

開單獨 ticket 加：

1. **Scope coverage check**：grep `@router.<method>` 與 `require_scope(...)` 配對，缺漏 raise
2. **MCP tool scope consistency**：tool.scopes ⊆ union(endpoint scopes for endpoint in tool.bundles)
3. **Allowlist consistency**：`app/auth/mcp_clients.py` 內所有 client 的 scope 是合法 scope id

---

## 6. 上游決策連結

| 來源 | 決定 | 影響本檔 §X |
|---|---|---|
| agent-interface Q5 sub-5a | 5 scope + narrow default + per-client 覆寫 | §2.1, §3.4 |
| agent-interface Q2 | 核心流程 packaging + CRUD 1:1 | §3.3 |
| agent-interface Q7 sub-7c | Allowlist 存 `app/auth/mcp_clients.py` | §5 |
| auth Q1 | Authentik (OSS) + Google Workspace upstream IdP | §2 token 來源 |
| auth Q4 | 簡化 dual-stack 1 sprint | §2 過渡期 require_scope 兼容 JWT/OAuth 兩種 token |
