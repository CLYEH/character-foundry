# Character Foundry — AI Model Integration

> **Status:** Draft v0.1 · 2026-04-23
> **Owner:** Backend Agent
> **Scope:** gpt-image-2 + Seedance 2.0 + Anthropic (reconciler) 的 client 設計

---

## 1. 整合原則

1. **每個模型一個 client class**，繼承共同 `AIClient` base
2. **抽象到 provider interface**，之後換模型不影響上層
3. **Retry / timeout / circuit breaker 在 base class**
4. **All async**（httpx async client）
5. **Secret 走環境變數**，絕不寫死

---

## 2. 抽象介面

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class ImageGenerationInput:
    prompt: str
    reference_images: list[bytes] | None  # PNG bytes
    mask: bytes | None  # PNG bytes (inpaint)
    seed: int | None
    aspect_ratio: str  # e.g. "1:1", "2:3"
    mode: str  # "text2image" / "image2image" / "inpaint"

@dataclass
class ImageGenerationResult:
    image_bytes: bytes
    content_type: str  # "image/png"
    width: int
    height: int
    model_name: str
    model_version: str
    seed_used: int
    cost_units: float
    raw_response: dict  # 存 GenerationLog 用

@dataclass
class VideoGenerationInput:
    prompt: str
    source_image: bytes  # PNG
    duration_seconds: float
    seed: int | None

@dataclass
class VideoGenerationResult:
    video_bytes: bytes
    content_type: str  # "video/mp4"
    duration_ms: int
    width: int
    height: int
    model_name: str
    model_version: str
    cost_units: float
    raw_response: dict


class AIClient(ABC):
    """Base class with retry / timeout / circuit breaker."""

    async def call_with_resilience(self, fn, *args, **kwargs):
        """Retry + timeout + circuit breaker wrapper."""
        ...


class ImageProvider(AIClient):
    @abstractmethod
    async def generate(self, input: ImageGenerationInput) -> ImageGenerationResult: ...


class VideoProvider(AIClient):
    @abstractmethod
    async def generate(self, input: VideoGenerationInput) -> VideoGenerationResult: ...
```

---

## 3. GptImage2Client（實作 ImageProvider）

### 3.1 Config

```
OPENAI_API_KEY=<env>
GPT_IMAGE_2_MODEL=gpt-image-2
GPT_IMAGE_2_TIMEOUT_MS=60000
GPT_IMAGE_2_MAX_RETRIES=4
```

### 3.2 主要 method

```python
class GptImage2Client(ImageProvider):
    async def generate(self, input: ImageGenerationInput) -> ImageGenerationResult:
        if input.mode == "text2image":
            return await self._call_text2image(input)
        elif input.mode == "image2image":
            return await self._call_image2image(input)
        elif input.mode == "inpaint":
            return await self._call_inpaint(input)
        else:
            raise ValueError(f"unknown mode: {input.mode}")

    async def _call_text2image(self, input):
        payload = {
            "model": self.model,
            "prompt": input.prompt,
            "size": self._aspect_to_size(input.aspect_ratio),
            "seed": input.seed,
            "n": 1,
            "quality": "hd",
        }
        return await self.call_with_resilience(
            self._http_post,
            "/v1/images/generations",
            payload,
        )

    async def _call_image2image(self, input):
        # OpenAI's variations / edits endpoint
        payload = {
            "model": self.model,
            "prompt": input.prompt,
            "image": input.reference_images[0],  # primary reference
            "size": self._aspect_to_size(input.aspect_ratio),
        }
        return await self.call_with_resilience(
            self._http_post_multipart,
            "/v1/images/edits",
            payload,
        )

    async def _call_inpaint(self, input):
        payload = {
            "model": self.model,
            "prompt": input.prompt,
            "image": input.reference_images[0],  # base image
            "mask": input.mask,                  # PNG alpha mask
            "size": self._aspect_to_size(input.aspect_ratio),
        }
        return await self.call_with_resilience(
            self._http_post_multipart,
            "/v1/images/edits",
            payload,
        )
```

### 3.3 Retry 規則

| 錯誤類型 | HTTP | Retry? | 策略 |
|---|---|---|---|
| Timeout | - | Yes | 指數退避 1s, 2s, 4s, 8s |
| 5xx | 500-599 | Yes | 指數退避 |
| 429 rate limit | 429 | Yes | 依 `Retry-After` header |
| 400 validation | 400 | No | 直接 fail，回 `VALIDATION_*` |
| 400 content policy | 400 with `content_policy_violation` flag | No | 回 `PROMPT_CONTENT_POLICY` |
| 401/403 auth | 401/403 | No | 回 `INTERNAL_AUTH_FAILED`（管理員要查 API key） |

### 3.4 Circuit Breaker

```python
class CircuitBreaker:
    # 5 連續失敗 / 1 分鐘 → OPEN 5 分鐘
    failure_threshold = 5
    failure_window_seconds = 60
    open_duration_seconds = 300

    # State: CLOSED / OPEN / HALF_OPEN
```

當 Breaker OPEN：
- 所有新 task 直接 fail with `MODEL_UNAVAILABLE`
- 在 Redis 寫入 degraded state：`degraded:gpt-image-2 = {reason: 'CIRCUIT_OPEN', retry_at: ...}`
- UI 透過 `/v1/meta` 讀 `degraded_services` 陣列顯示 banner：「gpt-image-2 暫時不可用，5 分鐘後自動恢復」

### 3.5 Degraded state 聚合（FB-1 實作）

每個 AI client 啟動時註冊自己到全域 registry。`/v1/meta` endpoint 查 Redis：

```python
# Redis keys: degraded:{service_name}
# Value: JSON { reason, retry_at, message }

async def get_degraded_services() -> list[dict]:
    services = ['gpt-image-2', 'seedance-2.0', 'reconciler']
    result = []
    for svc in services:
        state = await redis.get(f"degraded:{svc}")
        if state:
            result.append({"service": svc, **json.loads(state)})
    return result
```

Circuit breaker 關閉時刪除對應 Redis key，degraded_services 陣列自動縮短。

### 3.5 Content Policy 處理

當 OpenAI 回 content policy 拒絕：
- 不 retry
- 回 `PROMPT_CONTENT_POLICY` 錯誤
- UX 顯示使用者訊息：「內容涉及限制主題，請修改補述後重試」
- **不暴露** OpenAI 原始拒絕訊息（可能含內部資訊）

### 3.6 U2 partial preview 結論

**不支援**。Phase 1 用 estimated progress（詳見 `task-queue.md` §5.1）。

---

## 4. Seedance2Client（實作 VideoProvider）

### 4.1 Config

```
SEEDANCE_API_KEY=<env>
SEEDANCE_API_URL=https://api.seedance.example/v2
SEEDANCE_TIMEOUT_MS=180000       # 3 分鐘
SEEDANCE_MAX_RETRIES=2            # 影片重試很貴
```

### 4.2 Poll vs Webhook

Seedance 2.0 實際 API 行為待確認。可能是：
- **Sync**（等到影片生完才回）→ 用長 timeout
- **Async**（回 job_id，我們自己 poll）→ 需要我們的 worker 內部 poll
- **Webhook**（我們提供 callback URL）→ 需要 backend 有 public endpoint

**Phase 1 假設 async poll 模式**（最常見）：

```python
async def generate(self, input: VideoGenerationInput) -> VideoGenerationResult:
    # 1. 送 job
    job_id = await self._submit_job(input)

    # 2. Poll 狀態
    while True:
        status = await self._poll_status(job_id)
        if status.state == "completed":
            video_bytes = await self._download(status.video_url)
            return VideoGenerationResult(...)
        elif status.state == "failed":
            raise ProviderError(status.error)

        # Push progress via Redis pubsub
        await self._publish_progress(ctx, status.progress)

        await asyncio.sleep(3)
```

### 4.3 Duration 選擇

Preset motion 固定 3-5s，custom 3-10s：

```python
PRESET_DURATIONS = {
    "preset_wave": 3.5,
    "preset_nod": 3.0,
    "preset_gesture": 4.0,
    "preset_happy": 3.0,
    "preset_idle": 5.0,
}
```

Custom 由使用者描述推斷（或固定 5s，Phase 1 先這樣）。

### 4.4 Retry 規則

同 gpt-image-2，但 `max_retries=2`（影片生成貴，失敗時成本高）。

---

## 5. AnthropicClient（Prompt Reconciler 用）

### 5.1 Config

```
ANTHROPIC_API_KEY=<env>
RECONCILER_MODEL=claude-sonnet-4-6
RECONCILER_TIMEOUT_MS=30000
RECONCILER_MAX_RETRIES=3
```

### 5.2 主要 method

使用 Anthropic Messages API 的 JSON mode：

```python
class AnthropicReconcilerClient(AIClient):
    async def reconcile(self, input: ReconcileInput) -> ReconcileOutput:
        system = self._load_system_prompt()
        user_message = self._render_user_message(input)

        response = await self.call_with_resilience(
            self._client.messages.create,
            model=self.model,
            max_tokens=800,
            system=system,
            messages=[{"role": "user", "content": user_message}],
            response_format={"type": "json_object"},
        )

        data = json.loads(response.content[0].text)
        return ReconcileOutput(**data, llm_latency_ms=response.latency_ms)
```

### 5.3 Degraded mode

若 Anthropic API 全掛（5 連失敗）：
- Reconciler 降級為「純翻譯 + constraints append」
- 品質下降但不 block
- UX 顯示 banner：「Prompt 最佳化暫時不可用」

---

## 6. 成本追蹤

每次 AI call 成功後寫 `GenerationLog`：

```python
await db.insert_generation_log(
    user_id=task.user_id,
    character_id=character_id,
    entity_type=entity_type,
    entity_id=entity_id,
    model_name="gpt-image-2",
    model_version="v1.0",
    final_prompt=reconciled.final_prompt,
    parameters={"seed": seed, "aspect_ratio": "1:1"},
    cost_units=result.cost_units,
    status="success",
    started_at=task.started_at,
    completed_at=NOW(),
    duration_ms=elapsed_ms,
)
```

**Cost units 換算：**
- gpt-image-2: 1 call = 1.0 unit (hd) / 0.5 unit (standard)
- Seedance 2.0: 1 call = 10.0 units (vs image gen 昂貴 10x)
- Anthropic reconciler: 1 call ~ 0.01 unit（很便宜）

**UI 顯示**：乘上參考匯率（例 1 unit = $0.01 USD）顯示累計成本。

---

## 7. 密鑰管理

### 7.1 環境變數命名

```
# 核心 AI
OPENAI_API_KEY=<secret>
SEEDANCE_API_KEY=<secret>
ANTHROPIC_API_KEY=<secret>

# 應用層
JWT_SECRET=<secret>
STORAGE_SIGNED_URL_SECRET=<secret>
DATABASE_URL=postgresql+asyncpg://...
REDIS_URL=redis://...
```

### 7.2 輪替

- API key 換掉時：直接換環境變數 → 重啟服務
- JWT_SECRET 換掉 → 全使用者被踢，需重新登入（Phase 1 可接受）

### 7.3 存放

Phase 1 內網自架 → `.env` 檔 + `chmod 600` + 不進 git
Phase 2 → HashiCorp Vault / AWS Secrets Manager / 類似方案

---

## 8. Observability

### 8.1 每 AI call 記錄

- Request ID（跨 log 追蹤）
- Model name + version
- Input size（prompt tokens, image bytes）
- Output size
- Duration
- HTTP status code
- Error（若失敗）

### 8.2 Metrics（Prometheus-style）

```
character_foundry_ai_call_total{model="gpt-image-2", mode="text2image", status="success"}
character_foundry_ai_call_duration_ms{model="gpt-image-2", mode="text2image", quantile="0.5"}
character_foundry_circuit_breaker_state{model="gpt-image-2"}  # 0=closed, 1=half, 2=open
character_foundry_task_queue_depth{queue="image"}
```

DevOps Agent 配對應的 Grafana dashboard。

---

## 9. 測試策略

### 9.1 單元測試（mock provider）

```python
class FakeGptImage2Client(ImageProvider):
    """Returns predetermined fake images for unit tests."""
    async def generate(self, input):
        return ImageGenerationResult(
            image_bytes=FAKE_PNG_BYTES,
            ...
        )
```

### 9.2 Integration 測試（真 API，少跑）

CI 跑，每週一次：
- 發 10 個已知 prompt
- 確認有回圖、cost_units 合理
- 發 1 個違規 prompt，確認 content policy 錯誤處理對

### 9.3 Chaos 測試

Mock provider 刻意回：
- Timeout
- 500 errors
- Rate limit
- Content policy 拒絕

確認 retry / circuit breaker / error wrapping 都正確。

---

## 10. 關聯文件

- `prompt-reconciler.md` — 使用 AnthropicClient
- `task-queue.md` — Worker 內呼叫 image/video provider
- `api-shape.md` §4 錯誤格式 — 所有 provider error wrap 成 `AgentError`
- `../data/db-schema.md` GenerationLog — 成本追蹤寫入此表
