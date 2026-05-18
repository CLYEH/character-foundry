# T-087: MCP streamable HTTP `Last-Event-ID` resumability

**Status:** TODO
**Sprint:** 3.5b
**Est:** S
**Depends on:** T-080（MCP skeleton；event id 機制掛在 streamable HTTP transport 層）、T-086（motion.generate i2v 是最關鍵測試對象——長 task，斷線重連價值最高）
**Related:** T-082（nginx proxy_read_timeout；resumability 是 timeout 防線之外的另一層），T-084 / T-085（progress notification 都吃同一個 id 機制）

---

## Scope

把 agent-interface Round 1 Q3 實作 gotcha #3 落地：MCP streamable HTTP server 對每個送出的 SSE event 賦 monotonic id，client 重連時帶 `Last-Event-ID` header 可拿斷點之後的訊息，避免 i2v / long-running task 因斷線當 cancel 重跑。

**In scope:**

### Server side
- 對每個 streamable HTTP request（含 `notifications/progress` + 最終 result）的 SSE event 賦 monotonic id
- id 格式：`<request_id>:<seq>`（如 `req_abc123:0001`），其中 `request_id` 是 MCP request 唯一 id（由 client JSON-RPC envelope 帶來）、`seq` 是該 request 內的單調遞增整數
- 每個 request 的 event 序列在 server 端有 short-TTL buffer：
  - Buffer key = `request_id`
  - Buffer entries = `(seq, event_payload)`
  - TTL = 5 分鐘（涵蓋 i2v 場景 + 客戶端短暫網路斷線；過長浪費 memory）
  - 容量上限 per request：100 events（progress 推 5-10s 一條 × 2 分鐘 ≈ 24 條，留 buffer）
- Buffer 存哪：
  - **本單選 in-memory dict（per-process）**，理由：MCP server 是 same-process（per Q7 sub-7a），一個 client 重連通常打到同 process 同 server
  - 多 process / 多 instance 場景下會 miss（client 連 process A 中段、重連被 LB 導到 process B）→ 留為 known limitation，doc 寫清楚；Redis-backed buffer 是 Phase 2 升級
  - Phase 1 single instance 不撞這條

### Client reconnect 處理
- Client 重連時 HTTP header 帶 `Last-Event-ID: <request_id>:<seq>`
- Server 收到後：
  1. 解析 request_id + seq
  2. 從 buffer 查該 request_id 是否還在
  3. 若在 → 從 `seq + 1` 開始 replay 所有後續 events；replay 完成後接到 live stream（若 task 還沒完成）
  4. 若不在（過 TTL / 從未存在）→ 回 SSE event with `error` payload `{ code: "RESUME_NOT_AVAILABLE", message: "..." }`，client 自行決定重跑 / 放棄

### 套用範圍
- 所有 MCP tool 的 progress notification 都吃 id 機制（自動，由 transport 層處理，不需要 tool 改 code）
- 重點驗證對象：`motion.generate`（T-086，i2v 長 task）
- `character.create`（T-084）+ `alias.add`（T-085）也應該 benefit，但測試 fixture 不必每個 tool 都跑

### Tests
- `api/tests/mcp/test_resumability.py`：
  - **正例 1（buffer hit）**：
    - 起 `motion.generate` tool call，收到 3 條 progress event（seq 0001-0003）
    - 模擬 client 斷線（close connection）
    - 等 500ms 後重連，帶 `Last-Event-ID: <req_id>:0003`
    - 斷言：收到 seq 0004 開始的 events，最終 result 抵達，無 event 遺失
  - **正例 2（task 已完成 + buffer 還在）**：
    - tool call 完成（task done）後 30s 內重連 → server replay 所有 events 含最終 result
  - **負例 1（buffer TTL 過）**：
    - tool call 起步收 progress，斷線後 sleep 6 分鐘，重連
    - 斷言：收到 `RESUME_NOT_AVAILABLE` SSE event
  - **負例 2（不同 request_id）**：
    - 重連帶 unknown request_id → 同樣 `RESUME_NOT_AVAILABLE`
  - **edge case**：seq 100（buffer 上限）後再來一條 → 最舊那條被驅逐；client 重連帶 seq 0001 → `RESUME_NOT_AVAILABLE`（buffer 已 evict）

### Documentation
- `api/app/mcp/transport.md`（new，short doc 200 行內）：說明 id 格式、TTL、buffer 容量、Phase 2 升級 path（Redis）
- `planning/agent-interface/scope.md` §2.2 加一行 reference：「resumability 機制見 T-087 落地」

**Not in scope:**
- 改 MCP Python SDK 行為（id 機制應該由 SDK 本身 + transport 層配合實作；本單若發現 SDK 不支援，upstream PR 或本地 monkey patch + 註明）
- Redis-backed buffer（Phase 2）
- Cross-process / cross-instance resumability（Phase 2）
- Cancel semantics（per Q3 gotcha #3 spec 明確說斷線**不能**當 cancel——本單實作就是讓斷線真的不被當 cancel；cancel 是 explicit MCP request 才觸發）

---

## Planning refs

- `planning/agent-interface/open-questions.md` Round 1 Q3 實作 gotcha #3（`Last-Event-ID` resumability 是必要不是 nice-to-have）
- `planning/agent-interface/scope.md` §2.2（async task progress notification design）
- T-080 ticket（MCP server skeleton，本單 build on top）
- T-086 ticket（motion.generate i2v，最關鍵測試對象）
- MCP spec streamable HTTP: https://modelcontextprotocol.io/specification/2025-06-18/basic/transports

---

## Acceptance criteria

- [ ] Server 端每個 SSE event 有 monotonic id（格式 `<request_id>:<seq>`）
- [ ] In-memory buffer 機制：TTL 5 分鐘、容量 per request 100 events
- [ ] Client 帶 `Last-Event-ID` 重連能 replay 缺漏 events 並接回 live stream
- [ ] Buffer miss / TTL 過 / unknown request_id → `RESUME_NOT_AVAILABLE` SSE error event
- [ ] 5 條 resumability test 全綠（正例 2 + 負例 2 + edge case 1）
- [ ] `motion.generate` 真實 i2v 斷線測試（用 stub 模擬 30s task + 中段斷線 5s）通過
- [ ] `api/app/mcp/transport.md` 寫清 id 格式、TTL、known limitations
- [ ] Phase 2 升級 path（Redis backing）有 doc note，含預估工
- [ ] `planning/agent-interface/scope.md` §2.2 加 reference

---

## Files expected to touch

- `api/app/mcp/transport.py` (new or edit — 視 T-080 落地時是否已有 transport 層) — id 機制 + buffer
- `api/app/mcp/transport.md` (new) — short design doc
- `api/tests/mcp/test_resumability.py` (new)
- `planning/agent-interface/scope.md` (edit — §2.2 加 reference)
- `tickets/T-087-mcp-last-event-id-resumability.md` (new — 本單)
- `STATUS.md` (edit)

---

## OAuth scope required

`n/a`（不新增 endpoint；transport 層機制）

---

## MCP tool delta

`n/a`（所有 tool 都自動受惠，無 tool 新增 / 修 contract）

---

## Notes

- **為什麼 in-memory 不是 Redis**：MCP server same-process + Phase 1 single instance（per Q7 sub-7a），in-memory 已涵蓋 99% 重連場景；Redis 多一個 dependency、多一個 failure mode。Phase 2 multi-instance 時再升級
- **TTL 5 分鐘從哪來**：i2v 觀察 30-120s，5 分鐘涵蓋 task 完成後留客戶端 reasonable 重連時間；過長浪費 memory，過短大型 i2v 中段斷線會 miss
- **buffer 容量 100 events 從哪來**：5-10s 推一條 × 2 分鐘 = 12-24 條 progress + 1 result + 安全 buffer ≈ 100。i2v 不會推到 100 條（時間不夠）；character.create / alias.add 也遠低於
- **`Last-Event-ID` 是 SSE 標準 header**：W3C EventSource spec 內定義，browser native `EventSource` API 自動送，server side 處理是標準做法。MCP streamable HTTP 走 SSE-like 機制，header 名稱保持一致符合 spec convention
- **為什麼 id 含 `request_id`**：MCP 同一個 server 同時跑多個 tool call，每個 call 是獨立 stream；單純 monotonic seq 會跨 request 衝突。`<request_id>:<seq>` 避免跨 stream replay 拿到別人的 events
- **若 SDK 不支援自訂 transport hook**：本單 implementation 若發現 mcp Python SDK transport 層 close 到無法插 buffer hook → fork local + monkey patch + 上 upstream PR；不在本單範圍但 PR description 必須說明採取的方案
- **斷線當 cancel 的反例**：Q3 gotcha 明確說「斷線不能當 cancel」。Cancel 必須是 MCP `cancel` request 或 tool 內部 task 達終態。本單實作正是這條 invariant 的具體形式——server 不會因為 client 斷線就終止 task；buffer 機制保留 events 等重連
