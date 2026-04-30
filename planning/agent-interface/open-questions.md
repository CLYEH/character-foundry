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
