# T-087: MCP long-task disconnect-safety via async submit + poll-by-id

**Status:** TODO
**Sprint:** 3.5b
**Est:** M（原估 S；重新定義後同時改三個已出貨的 packaged tool 合約 + 測試，工量上修）
**Depends on:** T-080（MCP skeleton + dual-stack bearer）、T-084 / T-085 / T-086（三個 packaged generation tool，本單改它們的 async 合約）、T-088（`task.get` 是 agent 回來查狀態的工具）

> **2026-05-22 重新定義（使用者拍板）：本單從「SSE `Last-Event-ID` resumability」改為「async submit + poll-by-task-id」。**
>
> **原設計（已捨棄）：** 對每個 SSE event 賦 monotonic id、server 端 short-TTL buffer、client 帶 `Last-Event-ID` 重連補播缺漏 events。
>
> **為什麼捨棄：** MCP Python SDK（v1.27.1）的 `Last-Event-ID` resumability 只在 **stateful** 模式可用——`StreamableHTTPSessionManager` 在 `stateless=True` 時把 `event_store` 寫死成 `None`（`mcp/server/streamable_http_manager.py:183`）。但本專案的 MCP server 是 T-080 刻意選的 **stateless**（`app/mcp/app.py` `stateless_http=True`）。要做原設計 = 把整個 server 切 stateful（client 要先 `initialize` 拿 `mcp-session-id` 再每次帶上、改動 client 合約 + sticky routing），遠超原 scope；而且 SDK `EventStore` 介面只給 `str(request_id)` 當 stream key，本身就是原 ticket 安全要求禁用的非全域唯一值，要滿足安全 AC 還得 monkeypatch SDK transport。
>
> **新設計（本單）：** 不在 transport 層記任何狀態——**長任務本來就跑在獨立的 arq worker、狀態存在 DB 的 `tasks` row**，斷線根本不會取消工作（worker 照跑、照寫 row）。唯一的缺口是「長工具沒把 `task_id` 交給 agent」，所以斷線後 agent 無從查起。本單把缺口補上：長工具把 `task_id`（＋目標 entity id）交給 agent，agent 用既有的 `task.get` 回來查「好了沒」、好了再用對應 getter 拿成品。MCP server 維持 stateless，零 SDK 改動。

---

## Scope

讓三個 packaged generation tool 的長任務在 **MCP 連線中斷時不會白跑、可重新查詢**，做法是把「斷線後拿不到 `task_id`」這個缺口補上。

### `motion.generate`（T-086）→ 非阻塞 async submit
- enqueue i2v task 後**立刻回傳** `{ task_id, motion_id, status: "queued" }`，**不**再 block / poll / 串 progress。
- agent flow：`motion.generate` → `task.get(task_id)` 輪詢到 `status=completed` →（失敗則 `task.get` 的 `error` 帶 `MODEL_CONTENT_FILTERED` 等結構化 code）→ `motion.get(motion_id)` 拿影片成品。
- 斷線安全：worker 照跑；agent 用已持有的 `task_id` / `motion_id` 回來查，零遺失。
- 移除 `_wait_for_motion_task`、poll timeout、`running_i2v`/`finalizing` progress、`_agent_error_from_task`（失敗改由 `task.get` 的 `error` 欄位觀察，T-051 RAI 行為原樣保留——`task.error` 就是 worker 寫的 AgentError dict）。

### `alias.add`（T-085）→ 非阻塞 async submit
- mask 上傳（同步、快）保留；enqueue alias generation task 後**立刻回傳** `{ task_id, alias_id, status: "queued" }`，不再 poll generation。
- agent flow：`alias.add` → `task.get(task_id)` → `alias.get(alias_id)`。
- 移除 `_wait_for_alias_task`、generation poll/progress/timeout、`_agent_error_from_task`。tool-entry 驗證（`mask_file`/`mask_id` 互斥、inline `reference_images` 拒絕）+ mask 上傳 phase error 保留（那些是 enqueue 前的同步失敗）。

### `character.create`（T-084）→ 維持阻塞，但**提早把 recovery handle 交給 agent**
- 因為它是多步驟（建 session →（傳參考圖）→ 跑 checkpoint task → **select-base 收尾**），select-base 必須在 task 完成後由 server 跑，無法「丟一個 id 就閃人」。所以維持「一次呼叫做完、回傳完整 `CharacterCreateResult`」。
- 但在 session 建好後 + checkpoint task enqueue 後，各發一則 progress notification，`message` 帶 machine-readable recovery handle（JSON：`{phase, character_id, session_id, task_id?}`），讓**有訂閱 progress 的 agent** 在斷線時已持有 `character_id` / checkpoint `task_id`，可用 `task.get` / `character.get` / `character.get_session` 回來查狀態。
- 其餘行為（4-phase、abandon-on-failure、phase-tagged error）不變。

### 共通
- 三個 tool 的 registry `description` 改寫，明說新合約（「returns a task handle to poll」vs「blocks, emits a recovery handle」）。
- `task.get` 已能回 `status` / `entity_id` / `result` / `error`（T-088），無需改。
- MCP server 維持 stateless；不加 EventStore、不切 stateful、不 monkeypatch SDK。

**Not in scope:**
- SSE `Last-Event-ID` event replay / server-side event buffer（原設計，捨棄；若未來 multi-instance + 需要真 SSE 補播再評估，前提是先切 stateful）。
- 改 MCP server stateless→stateful。
- Webhook 完成通知（api-shape §3.4，Phase 2）。
- 對 `character.create` 把 select-base 搬進 worker 改成單任務（評估過，會動到 checkpoint task 語意 + 連帶 REST checkpoint 流程，風險大；本單採「提早給 id」的低風險路徑）。

---

## Planning refs

- `planning/agent-interface/scope.md` §2.2（async task 模型——本單把「不要逼 agent polling」這條原則對長任務放寬，理由：blocking+progress 撐不過斷線，durable task row + poll 才 robust；progress 對連線中的 client 仍是 optimization）
- `planning/agent-interface/endpoint-mcp-mapping.md` §3（packaged tool 的 async 模型描述，需同步）
- `planning/agent-interface/open-questions.md` Round 1 Q3（async task 通知；原 gotcha #3 的 `Last-Event-ID` 結論被本單取代，留 note）
- T-086 / T-085 / T-084 ticket（被本單改合約的三個 tool）
- `app/schemas/task.py` `TaskDTO`（`entity_id` = 完成後的 motion/alias id；`error` = AgentError dict，T-051 RAI code 可見）

---

## Acceptance criteria

- [ ] `motion.generate` enqueue 後立刻回傳 `{task_id, motion_id, status}`，不 block；不再有 poll loop / progress / timeout
- [ ] `alias.add` 同步做完 mask 上傳後 enqueue，立刻回傳 `{task_id, alias_id, status}`，不 block generation
- [ ] `character.create` 維持阻塞回傳完整結果，但在 session 建立 + checkpoint enqueue 後發出帶 recovery handle（`character_id` / `session_id` / checkpoint `task_id`）的 progress notification
- [ ] 三個 tool 的 registry `description` + schema 反映新合約；`output_schema` 對 motion/alias 換成 task-handle 型別
- [ ] i2v 失敗（RAI）路徑：`motion.generate` 仍成功 enqueue + 回傳 handle；失敗由 `task.get(task_id).error` 觀察為 `MODEL_CONTENT_FILTERED`（測試釘住）
- [ ] `motion.generate` / `alias.add` 的 enqueue-前同步驗證失敗（parent not-found / 403 / 名稱衝突 / mask 衝突等）仍以結構化 ToolError 回（不需 task_id）
- [ ] tests：`test_motion_generate.py` / `test_alias_add.py` 改寫成斷言「回 handle + task 已建 + 同步驗證失敗路徑」；`test_character_create.py` 加斷言早期 recovery-handle progress；conftest inline-arq harness 相應調整
- [ ] 3 個 MCP guardrail script 綠（tool scopes / coverage / clients）
- [ ] `mypy app/` clean、`ruff` clean、backend 全測綠
- [ ] planning：`scope.md` §2.2 + `endpoint-mcp-mapping.md` §3 + `open-questions.md` Q3 同步成新 async 模型；`STATUS.md` 更新

---

## Files expected to touch

- `api/app/mcp/schemas/motion.py`（+ `MotionGenerateResult`）
- `api/app/mcp/schemas/alias.py`（+ `AliasAddResult`）
- `api/app/mcp/tools/motion.py`（motion.generate 改非阻塞）
- `api/app/mcp/tools/alias.py`（alias.add 改非阻塞）
- `api/app/mcp/tools/character.py`（早期 recovery-handle progress）
- `api/tests/mcp/tools/test_motion_generate.py` / `test_alias_add.py` / `test_character_create.py`
- `api/tests/mcp/tools/conftest.py`（inline-arq harness）
- `planning/agent-interface/scope.md` / `endpoint-mcp-mapping.md` / `open-questions.md`
- `tickets/T-087-*.md`（本單）、`STATUS.md`

---

## OAuth scope required

`n/a`（不新增 endpoint；改的是 MCP tool 的回傳合約 + 一則 progress notification）

---

## MCP tool delta

- `motion.generate` output：`MotionDetailResponse` → `MotionGenerateResult { task_id, motion_id, status }`
- `alias.add` output：`AliasResponse` → `AliasAddResult { task_id, alias_id, status }`
- `character.create`：output 不變（`CharacterCreateResult`）；行為加一則早期 recovery-handle progress

---

## Notes

- **斷線當 cancel 的反例（原 ticket invariant，仍成立）**：斷線不會取消 task——work 跑在 arq worker，MCP 連線只是觀察者。新設計讓這條 invariant 變得顯而易見：tool 根本不 hold 連線（motion/alias），或 hold 但 work 仍獨立於連線（character）。
- **為什麼 motion/alias 走純 async、character 走 hybrid**：前兩者 = 一個 task 跑完 entity 就存在，「丟 id 就閃人」乾淨；character 有 task 後的 select-base 收尾，無法純 async，故保留阻塞 + 提早給 id。使用者 2026-05-22 拍板。
- **「不要逼 agent polling」原則的放寬**：scope.md §2.2 原寫「async task 走 progress + 完成回 result，不要逼 agent polling」。本單對長任務放寬——polling `task.get` 換來斷線安全。連線中的 client 仍可（character.create）收 progress 當 optimization，但 canonical / disconnect-safe path 是 poll-by-id。
- **T-051 RAI 安全保留**：`motion.generate` 不再 inline 重建 worker error，但 `task.get` 的 `error` 欄位就是 worker 寫的 AgentError dict，agent 輪詢時看得到 `MODEL_CONTENT_FILTERED` 並可 retry/rephrase，與 REST 一致。
