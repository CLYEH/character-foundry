# Agent Interface — Open Questions

> 待決策清單。回答前不要動實作。

---

## Q1. MCP transport 選哪種？

| 候選 | 優點 | 缺點 |
|---|---|---|
| Streamable HTTP | remote 標配、JWT/OAuth header 直接接、SSE 內建 | 連線狀態管理較複雜 |
| stdio | 最簡單、無 auth 負擔 | 只適合單客戶 / 本機 |
| WebSocket | 雙向流暢 | MCP spec 非主流 |

**建議：streamable HTTP**（Phase 1 內網但 agent 可遠端呼叫，未來開放也順）。

---

## Q2. Tool 顆粒度策略

REST 端點 1:1 wrap vs packaging 成「agent 一呼叫完成一件事」？

- 1:1：簡單、自動生；agent 要自己組合
- packaging：多寫工，但 agent UX 好

**建議：核心流程 packaging**（`create_character` 一條呼叫含 session + checkpoint + base lock）；CRUD endpoint 保 1:1。

---

## Q3. Async task agent 體驗

agent 怎麼知道 i2v / image gen 進度？

- Option A：MCP tool 直接 await（tool 阻塞到完成；MCP `progress` notification 推進度）
- Option B：tool 立刻回 task_id，agent 自己再呼 `get_task_status`
- Option C：tool 回 task handle，agent subscribe 到事件流

**建議：Option A + progress notification**。Agent 看到的是「同步呼叫，內部慢」，不用自己組 polling logic。

---

## Q4. Tool naming convention

- `character.create` / `character.add_alias` / `motion.generate`（dotted, namespace 分明）
- `create_character` / `add_alias` / `generate_motion`（snake_case, 一級平展）
- 既有 OpenAI / Anthropic SDK 慣例 vs MCP 慣例

**建議：dotted namespace**——agent 客戶端容易組成階層。

---

## Q5. Agent vs human scope 模型

例：`character:write` 給 owner，agent 該不該預設拿到？

- 嚴格：agent 只能拿 user 明確授權的 scope
- 寬鬆：agent 拿到的 token 等同 user，凡是 user 能做 agent 也能做

**建議：嚴格**——避免 agent 越權；human user 要明確同意 agent scope。但 Phase 1 只有 single team，scope 模型可極簡（user-level + agent-level 兩 tier）。

---

## Q6. Signed URL 在 OAuth 下怎麼遷？

既有 storage signed URL token 是 `STORAGE_SIGNED_URL_SECRET` 派生的 JWT。OAuth 後：

- 繼續用獨立 JWT（短 TTL，與 OAuth 平行）
- 改用 OAuth 授權的 storage token（一條 chain）
- 用 presigned URL pattern（S3 等對象存儲常見，但 Phase 1 用 LocalFilesystemBackend）

**建議：Phase 1 繼續獨立 JWT**——LocalFilesystemBackend 已習慣這種模型，OAuth integration 留給 storage 換 S3 那次重新評估。

---

## Q7. MCP server 暴露給誰

- 只內網？
- nginx + OAuth gating 給特定 client_id？
- 公開（registry 上架）？

**建議：Phase 1 內網 + OAuth client_id allowlist**。Phase 2 再評估外部 agent 進駐。

---

## Q8. 版本化策略

- MCP tool schema 與 `/v1` REST 同版本號
- 獨立版本號（agent surface 自己 v1 / v2）

**建議：獨立**——REST 改造時 MCP tool 可能不必跟著動（packaging 邏輯吸收差異），獨立版本號避免 lockstep。

---

## Q9. 哪些 REST endpoint 不會包進 MCP？

候選黑名單：

- `/health`（運維用，agent 沒理由呼）
- `/v1/auth/*`（OAuth 接管）
- 純 UI-driven endpoint（如 `/v1/exports/{id}/download` 走 redirect 而非結構化回應）

需要 review 一遍 `api-shape.md` §5.X 列出 black/white list。

---

## 決策時點

建議在 M3 收尾時（Sprint 3 W-G ship 後）跟使用者過一次這份清單，定案後才動 implementation。

---

## 決策紀錄

### Round 1（2026-05-03，agent-interface agent 視角，user confirmed）

| Q | Decision | 備註 |
|---|---|---|
| **Q1** | **Streamable HTTP** | OAuth Bearer header 直接接，SSE 內建。耦合 auth Q8 |
| **Q2** | **混合：核心流程 packaging + CRUD 1:1** | packaged tool: `character.create` / `character.add_alias` / `motion.generate` / `character.export`；CRUD（list / get / rename / delete）保 1:1 |
| **Q3** | **Option A — tool 阻塞 + MCP `notifications/progress`**（⚠ 長任務於 T-087 改為 async-submit + poll，見下方 gotcha #3）| 走 MCP first-class primitive，agent 不必感知 task system。實作 gotcha 見下方 |
| **Q4** | **Dotted namespace**（例：`character.create`） | 可組成階層；避免 snake_case 平展長期擠名 |

### Q3 實作 gotcha（Sprint 3.5b 開單時必處理）

1. **Python SDK 版本 pin**：`mcp` 套件須 ≥ 包含 PR #2038 的 release（2026-02-18 merge，修 `ctx.report_progress()` 在 streamable HTTP 下 `related_request_id` 漏帶導致 notification 走錯 stream）。Smoke test 必須**真的**斷言 notification 跨 streamable HTTP 有送達，不只靠 SDK 自家 unit test。
2. **nginx `proxy_read_timeout` ≥ 180s** for `/mcp` 路徑。i2v 跑 30–120s，nginx 預設 60s 會在 SSE stream 中段剪斷。step 4 devops 必須處理（比照既有 `/v1/tasks/{id}/stream` 配置）。
3. **~~`Last-Event-ID` resumability~~ → 改為 async-submit + poll-by-task-id（T-087，2026-05-22 重新定義）。** 原結論：server 對每個 SSE event 賦 monotonic id、client 重連帶 `Last-Event-ID` 補播。**捨棄原因**：MCP Python SDK 的 `Last-Event-ID` resumability 只在 stateful 模式可用（`event_store` 在 stateless 被寫死成 None），而本專案 MCP server 是 T-080 刻意選的 stateless；切 stateful 會改 client 合約且超出 scope。**新做法**：`motion.generate` / `alias.add` 改 **非阻塞**——回傳 `{task_id, entity_id, status}` handle，agent 用 `task.get` 輪詢、再用 getter 拿成品；`character.create` 維持 blocking 但提早發 `recovery_handle` progress。斷線不當 cancel 的 invariant 仍成立（work 跑在 arq worker、狀態存 DB tasks row，與連線解耦），且更穩——tool 根本不長 hold 連線。落地見 T-087；Q3 決策列的「tool 阻塞」對長任務不再適用（只 `character.create` 還阻塞）。

### Round 2（2026-05-07，agent-interface agent 視角，user confirmed）

**前提決策（refocus）**：Phase 1 同時支援兩條 grant：
- **Delegation**（Auth Code + PKCE）— 給 human user + 人授權的 agent（例：你授權 Claude Code 用你身分）
- **M2M**（Client Credentials）— 給 headless agent（例：`cf-test-agent` 跑 CI smoke、未來 batch cleanup）

| Q | Decision | 備註 |
|---|---|---|
| **Q5** | **Strict tier 模型 + 3 sub（見下）** | 耦合 auth Q3（scope 清單）、auth Q5（signed URL 解耦） |
| **Q6** | **Lock：signed URL 維持獨立 JWT，與 OAuth 完全解耦** | `STORAGE_SIGNED_URL_SECRET` 不動；TTL 7d 不縮（人貼 URL 到 Notion / Slack 場景）；S3 cutover 時再評估 |
| **Q7** | **3 sub（見下）** | 耦合 devops（docker stack 不多 service）+ nginx config |

#### Q5 sub-decisions

- **5a. M2M scope 顆粒度** → **Narrow default + per-client 覆寫**。Allowlist 內定 `M2M_DEFAULT_SCOPES = ["character:write", "task:read"]`；個別 client（如 `cf-test-agent`）在 `app/auth/mcp_clients.py` 顯式覆寫拿全 5 個 scope。理由：default 安全（不熟的 agent 自動 narrow），CI 等需要全套的顯式覆寫，2 行 config。
- **5b. Delegated agent token TTL** → **1h、無 refresh**。理由：delegation = agent 借 user 身分行動，30d refresh 等於把「代刷權」交出去 30 天，blast radius 太大；1h 一次性符合 agent 任務型用法。常駐 agent 場景出現再評估開 refresh。
- **5c. `*:admin` scope** → **不開**。理由：Phase 1 single user single team 沒 admin/member 分層；Phase 2 multi-team 時 scope 整體要重設計，預留省不到工。

#### Q7 sub-decisions

- **7a. MCP server 位置** → **Same-process**（FastAPI sub-app `/mcp`）。理由：共用 DB session / AgentError / task 系統，無跨網路 overhead，docker stack 不多 service；獨立 container Phase 1 過早優化。
- **7b. 網路邊界** → **LAN 可達**（nginx 不限 IP）。理由：security 靠 OAuth + allowlist 守，不靠 IP boundary；之後從手機 / 別台機器 demo 不會卡。
- **7c. Client 註冊** → **Pre-registered allowlist（Figma 模式）**。Allowlist 存 `app/auth/mcp_clients.py` module-level dict（同時涵蓋 delegated client 與 M2M client）。Phase 1 預定登記：`claude-code` / `vs-code` / `cursor` / `cf-test-agent`。理由：Phase 1 client < 5，DCR 開等於任何 agent 自我註冊就能進，反而擴大攻擊面；OAuth 2.1 spec 允許不開 DCR。

### Round 3（2026-05-07，agent-interface agent 視角，user confirmed）

| Q | Decision | 備註 |
|---|---|---|
| **Q8** | **Independent versioning** — MCP tool surface 自己 v1 / v2，與 REST `/v1` 解耦 | 核心 packaging tool 吃合多個 REST endpoint，REST 增刪欄位 MCP tool 內部可吸收；REST `/v2` 不強迫 MCP 一起跳 |
| **Q9** | **Blacklist by category**（先定原則，實作時 enumerate） | 細目落地在 Sprint 3.5b 對 `api-shape.md §5.X` 逐條 review |

#### Q9 分類

- ❌ **Ops**：`/health`、未來監控 endpoint
- ❌ **Auth**：`/v1/auth/*`（OAuth 接管，agent 不需要看 login flow）
- ❌ **Pure-UI redirect**：例 `/v1/exports/{id}/download` 走 302 redirect，agent 用 signed URL 直接抓即可
- ✅ **Whitelist 範疇**：`/v1/characters/*`、`/v1/aliases/*`、`/v1/motions/*`、`/v1/tasks/*`、`/v1/usage/*`、`/v1/meta`

遇到不確定要不要包的 endpoint 列為待決，Sprint 3.5b 開單前對齊。

---

## Step 1 完成（2026-05-07）

9 條 open-questions 全部拍板（Round 1 Q1-Q4 / Round 2 Q5-Q7 / Round 3 Q8-Q9）。下一步切換到 auth agent 視角，過 `../auth/open-questions.md` 8 條（Step 2）。

---

## 關聯來源

- MCP spec progress: https://modelcontextprotocol.io/specification/2025-06-18/basic/utilities/progress
- MCP spec streamable HTTP: https://modelcontextprotocol.io/specification/2025-06-18/basic/transports
- Python SDK fix: https://github.com/modelcontextprotocol/python-sdk/pull/2038 (merged 2026-02-18)
