# Character Foundry — Task Queue & Async Execution

> **Status:** Draft v0.1 · 2026-04-23
> **Owner:** Backend Agent
> **Resolves:** UX's U1 / U3 / U4 / U5, and a Data schema gap

---

## 1. 技術選型

### 1.1 Queue

**選擇：arq（async-native Redis-backed）**

**理由：**
- Native async（配 FastAPI + asyncpg）
- 輕量，只需 Redis
- 支援 retry / priority / deferred
- Redis 已經需要（reconciler cache），不增加額外基礎設施
- 比 Celery 輕、比 RQ 更 async-friendly、比 pgqueuer 更成熟

**替代方案：**
- Celery + Redis：更多功能但 overkill
- pgqueuer：不需要 Redis 但社群小
- Postgres LISTEN/NOTIFY：最少依賴但缺 retry / cancel

### 1.2 Broker

**選擇：Redis 7+**（單實例 Phase 1）

同時擔任：
- arq queue
- Reconciler cache
- Rate limit counter
- Queue position 查詢

---

## 2. Tasks Table

**✅ 已 patch 到 `../data/db-schema.md` §3.11（Data Agent iteration 2, 2026-04-23）。**

完整 DDL 以 `db-schema.md` 為準。此處保留**概覽版**作為 Backend 視角的參考：

### 2.1 Schema 概覽

```sql
CREATE TABLE tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,

    task_type VARCHAR(50) NOT NULL
        CHECK (task_type IN (
            'create_checkpoint',
            'create_alias',
            'create_motion',
            'export_zip',
            'copy_character'
        )),

    status VARCHAR(20) NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled')),

    -- 關聯到最終產物（若有）
    entity_type VARCHAR(30),  -- 'checkpoint' / 'alias' / 'motion' / 'character' / 'export'
    entity_id UUID,

    -- 進度 0.0-1.0（null = 不支援 progress）
    progress REAL,

    -- 估計完成時間（ms）
    estimated_duration_ms INT,

    -- 輸入 / 參數（存成 JSON 方便 retry）
    input_payload JSONB NOT NULL,

    -- 結果 / 錯誤
    result JSONB,          -- 成功時 = entity 摘要
    error JSONB,           -- 失敗時 = AgentError

    -- 時序
    queued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,

    -- 取消機制
    cancel_requested BOOLEAN DEFAULT FALSE,
    cancel_requested_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_tasks_user_status ON tasks(user_id, status, created_at DESC);
CREATE INDEX idx_tasks_status_queued ON tasks(status, queued_at)
    WHERE status IN ('queued', 'running');
CREATE INDEX idx_tasks_entity ON tasks(entity_type, entity_id)
    WHERE entity_id IS NOT NULL;

-- Completed / failed 24h 後可清理（scheduled job）
```

### 2.2 存活策略

- `queued` / `running`：無 TTL
- `completed` / `failed` / `cancelled`：保留 **24 小時**（UX 規格）
- 每小時 scheduled job `DELETE WHERE status IN ('completed','failed','cancelled') AND completed_at < NOW() - INTERVAL '24 hours'`

---

## 3. Task 生命週期

### 3.1 狀態轉換

```
[queued] ──worker pick──▶ [running]
    │                         │
    │                         ├──success──▶ [completed]
    │                         │
    │                         ├──error────▶ [failed]
    │                         │
    │                         └──cancel────▶ [cancelled]
    │
    └──user cancel (before pick)──▶ [cancelled]
```

### 3.2 建立流程

```python
async def create_task(
    user_id: UUID,
    task_type: str,
    input_payload: dict,
) -> Task:
    # 1. 估 estimated_duration
    estimated = await estimate_duration(task_type, input_payload)

    # 2. 寫 DB tasks 表（事實來源）
    task = await db.insert_task(
        user_id=user_id,
        task_type=task_type,
        input_payload=input_payload,
        estimated_duration_ms=estimated,
        status='queued',
    )

    # 3. 推 arq queue
    await arq_pool.enqueue_job(
        f"run_{task_type}",
        task_id=str(task.id),
    )

    return task
```

### 3.3 Worker 執行

```python
async def run_create_alias(ctx, task_id: str):
    task = await db.get_task(task_id)

    # 檢查是否已被 cancel
    if task.cancel_requested:
        await db.update_task(task_id, status='cancelled')
        return

    await db.update_task(task_id, status='running', started_at=NOW())

    try:
        # 1. Reconcile prompt
        reconciled = await reconciler.reconcile(...)

        # 2. 呼叫 gpt-image-2（非同步、可取消）
        result = await ai_client.generate(...)

        # 3. 寫 storage
        image_key = await storage.put(...)

        # 4. 寫 DB（aliases 表）+ 更新 task
        alias = await db.insert_alias(...)
        await db.update_task(
            task_id,
            status='completed',
            entity_type='alias',
            entity_id=alias.id,
            result={"alias": alias_dto(alias)},
            completed_at=NOW(),
        )
    except CancelledByUserError:
        await db.update_task(task_id, status='cancelled')
    except Exception as e:
        agent_error = wrap_error(e)
        await db.update_task(
            task_id,
            status='failed',
            error=agent_error.to_dict(),
            completed_at=NOW(),
        )
```

### 3.4 SSE 推送

Worker 在執行過程中透過 Redis pub/sub 推進度：

```python
await redis.publish(f"task:{task_id}", json.dumps({
    "status": "running",
    "progress": 0.3,
    "message": "正在呼叫 gpt-image-2...",
}))
```

SSE endpoint 訂閱該 channel，把訊息轉發給 client：

```python
@app.get("/v1/tasks/{task_id}/stream")
async def stream_task(task_id: UUID):
    async def event_gen():
        pubsub = redis.pubsub()
        await pubsub.subscribe(f"task:{task_id}")

        # 先推 initial state
        task = await db.get_task(task_id)
        yield f"data: {json.dumps(task.to_sse())}\n\n"

        if task.status in ('completed', 'failed', 'cancelled'):
            return  # 已結束，直接結束 stream

        # 訂閱更新
        async for msg in pubsub.listen():
            if msg['type'] == 'message':
                yield f"data: {msg['data']}\n\n"
                payload = json.loads(msg['data'])
                if payload['status'] in ('completed', 'failed', 'cancelled'):
                    break

    return StreamingResponse(event_gen(), media_type="text/event-stream")
```

---

## 4. U1 解：Estimated Duration

### 4.1 策略

**動態估計**：每個 task_type 查歷史 `GenerationLog` 的 p50 duration。

```python
async def estimate_duration(task_type: str, input_payload: dict) -> int:
    # 查最近 50 筆成功 task 的 duration
    durations = await db.fetch_recent_successful_durations(
        task_type=task_type,
        limit=50,
    )

    if len(durations) < 5:
        # 歷史不夠，用 hardcoded default
        return DEFAULT_ESTIMATES[task_type]

    return int(statistics.median(durations))
```

### 4.2 Hardcoded defaults（冷啟動用）

| Task Type | Default (ms) | 備註 |
|---|---|---|
| `create_checkpoint` | 15000 | gpt-image-2 text2image |
| `create_alias` | 20000 | gpt-image-2 image2image / inpaint |
| `create_motion` | 60000 | Seedance 2.0 i2v |
| `export_zip` | 10000 + 2000 × motion_count | 多 motion 多等 |
| `copy_character` | 3000 | 幾乎都在 DB + hardlink |

### 4.3 回傳給 UI

Task DTO 的 `estimated_duration_ms` 欄位。UI 顯示：
- 若 `progress` 有值：progress bar + 剩餘時間 `(1 - progress) × (elapsed / progress)`
- 若 `progress` 為 null：顯示 `estimated_duration_ms - elapsed` 的倒數

---

## 5. U2 解：Partial Preview

### 5.1 gpt-image-2

**Phase 1 假設：不支援 partial preview**（保守處理）。

理由：OpenAI API 雖有 streaming，但 image model 的 streaming 是 chunked bytes，不能解碼成預覽圖。除非模型層支援多階段輸出（low-res → high-res），否則 partial preview 只能是 placeholder。

**SSE 行為：** 只推 status 變化 + 估計 progress（用 elapsed / estimated 推算）。

```python
# Worker 端每 2s 推一次估算 progress
async def progress_estimator_loop(task_id, estimated_ms, started_at):
    while task_still_running(task_id):
        elapsed = now() - started_at
        estimated_progress = min(0.95, elapsed / estimated_ms)
        await redis.publish(f"task:{task_id}", {
            "status": "running",
            "progress": estimated_progress,
        })
        await asyncio.sleep(2)
```

### 5.2 Seedance 2.0

Video model 通常完全不 stream。同 gpt-image-2 處理。

### 5.3 未來升級

若 gpt-image-2 v2 加支援（或換 Flux 類模型），SSE schema 加 `partial_preview_url` 欄位即可，不影響已有 client。

---

## 6. U3 解：Queue Position

### 6.1 查詢

```python
async def get_queue_position(task_id: str) -> int | None:
    # arq 支援查詢 queue depth
    queued = await arq_pool.queued_jobs()

    for i, job in enumerate(queued, start=1):
        if job.job_id == task_id:
            return i
    return None  # Already running / not in queue
```

### 6.2 顯示時機

- UI 在 task 建立後立刻呼 `GET /v1/tasks/{id}` 拿狀態
- 若 `status == 'queued'`，Task DTO 含 `queue_position`
- UI 顯示：「排隊中 #3」
- 若 queue 中位置 = 1，顯示「即將開始」
- 進入 `running` 後 queue_position 變 null，UI 切換到 progress bar

### 6.3 SSE 推送 queue_position（FB-2 實作）

**Worker 端機制：**

1. Task 建立時：backend 計算當前 queue 深度，把 task 的 queue_position 寫入 Redis（例：`task:{id}:queue_pos = 3`）
2. Scheduled job（每 3 秒跑）掃 Redis 的所有 queued task：
   - 重新計算每個 task 的 queue_position（依 arq queued_jobs 順序）
   - 若變化 → 透過 Redis pub/sub 推 SSE event
3. Task 進 running → 從 Redis 刪除 queue_pos key，推最後一次 `{ status: 'running' }`

```python
async def publish_queue_positions_loop():
    while True:
        queued = await arq_pool.queued_jobs()
        for i, job in enumerate(queued, start=1):
            task_id = job.job_id
            prev = await redis.get(f"task:{task_id}:queue_pos")
            if prev != str(i):
                await redis.set(f"task:{task_id}:queue_pos", i, ex=300)
                await redis.publish(f"task:{task_id}", json.dumps({
                    "status": "queued",
                    "queue_position": i,
                }))
        await asyncio.sleep(3)
```

**Frontend：** 收到 SSE `queue_position` 事件直接更新 UI，不用額外 polling。

### 6.3 當 queue 滿載

Phase 1 假設單機 worker（concurrency = 2-4），不大容易塞。若 queue 超過 10，backend 在 task 建立時 reject with `QUEUE_FULL`。

---

## 7. U4 解：Task Cancel

### 7.1 取消時機分兩類

**Case A：task 還在 queue（未執行）**
```python
async def cancel_task(task_id: str, user_id: str):
    task = await db.get_task(task_id)

    if task.status == 'queued':
        # 從 arq queue 直接移除
        await arq_pool.abort_job(task_id)
        await db.update_task(task_id, status='cancelled', cancel_requested=True)
        return
```
**立刻取消**，UI 顯示「已取消」。

**Case B：task 已 running**
```python
    elif task.status == 'running':
        # 設 flag，worker 在 checkpoint 處 poll
        await db.update_task(
            task_id,
            cancel_requested=True,
            cancel_requested_at=NOW(),
        )
        await redis.publish(f"task:{task_id}:cancel", "1")
        # 不立刻變狀態，等 worker 回報
```

Worker 會在幾個 checkpoint 檢查 cancel flag：
- Reconciler call 前
- AI generation call 前
- Storage write 前

**但 AI API call 一旦送出無法中斷**。若 cancel 在 AI call 進行中，task 仍會完成 AI call，但：
- 不 commit DB 結果
- 把結果丟棄（或記 log 但不建 entity）
- Status 改 `cancelled`
- UI 顯示：「已取消（部分成本仍計入）」

### 7.2 UI 狀態流

```
[running] →「取消中...」(cancel_requested=true, status 還是 running)
         → [cancelled]（worker 回報）
         或 → [completed] / [failed]（worker 來不及取消）
```

UX 要處理「取消可能失敗」的情境：使用者按取消 → 可能仍成功。

---

## 8. U5 解：Copy Task

### 8.1 流程

```python
async def run_copy_character(ctx, task_id: str):
    task = await db.get_task(task_id)
    src_character_id = task.input_payload['src_character_id']
    new_name = task.input_payload['new_name']

    async with db.transaction():
        # 1. 建新 Character
        new_char = await db.insert_character(
            owner_id=task.user_id,
            name=new_name,
            copied_from_character_id=src_character_id,
        )

        # 2. Copy base
        src_base = await db.get_base_by_character(src_character_id)
        new_base_key = f"characters/{new_char.id}/base.png"
        await storage.copy(src_base.image_key, new_base_key)  # hardlink

        new_base = await db.insert_base(
            character_id=new_char.id,
            image_key=new_base_key,
            # 注意：from_checkpoint_id 可以 NULL 或指向原 checkpoint
            # 決議：保持指向原 checkpoint（唯讀引用，可追溯）
            from_checkpoint_id=src_base.from_checkpoint_id,
        )

        await db.update_character(new_char.id, base_id=new_base.id)

        # 3. Copy aliases（不含 motions，per B1）
        src_aliases = await db.get_aliases_by_character(src_character_id)
        for src_alias in src_aliases:
            new_alias_key = f"characters/{new_char.id}/aliases/{...}.png"
            await storage.copy(src_alias.image_key, new_alias_key)

            await db.insert_alias(
                character_id=new_char.id,
                name=src_alias.name,
                prompt=src_alias.prompt,
                user_freeform_note=src_alias.user_freeform_note,
                input_mode=src_alias.input_mode,
                image_key=new_alias_key,
                # 不複製 generation_log_id（新 alias 沒有 log）
            )

    await db.update_task(
        task_id,
        status='completed',
        entity_type='character',
        entity_id=new_char.id,
        result={"character_id": str(new_char.id), "new_character_slug": new_char.slug},
    )
```

### 8.2 預估時間與 progress

- 大部分 Copy 是**瞬間**的（hardlink + DB insert）
- 10 aliases 大約 <500ms
- 50 aliases 大約 <2s
- **不提供 progress**（task DTO `progress = null`）
- UI 用 indeterminate spinner + 「複製中...」文案

### 8.3 失敗處理

Transaction 失敗 → DB 整筆 rollback，storage 已 hardlink 的檔案靠 weekly reconciliation 清理（lifecycle.md §5.3）。

---

## 9. 任務優先級 / 併發

### 9.1 Phase 1 預設

- Worker concurrency：**4**（單機）
- 無優先級區分（FIFO）
- 重 task（i2v 60s）跟輕 task（copy 3s）共用 worker pool

### 9.2 Phase 2 未來優化

- 分 queue（`image_queue` vs `video_queue`），worker 配不同 concurrency
- 加 priority：使用者互動 task（checkpoint）優先於批次 task（export）
- 分散到多 worker node

---

## 10. Rate Limiting

### 10.1 每使用者

Redis token bucket，per user：
- Image gen：10 req/min, 200 req/day
- Video gen：5 req/min, 50 req/day（昂貴）
- Export：3 req/min（成本低但 I/O 重）
- 其他：60 req/min

### 10.2 全平台

保護外部 API quota：
- gpt-image-2 全局：50 req/min
- Seedance 2.0 全局：20 req/min

超過 → backend 把 task 卡在 queue，等 token 放出來再執行（不立刻 reject）。

---

## 11. 關聯文件

- `api-shape.md` — Task endpoints 定義
- `prompt-reconciler.md` — Worker 內呼叫 reconciler
- `ai-integration.md` — Worker 內呼叫 AI client
- `../data/db-schema.md` — **需補充 `tasks` 表 schema（§2）**
- `../data/lifecycle.md` — Task 24h 清理規則
