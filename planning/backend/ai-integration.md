# Character Foundry — AI Model Integration

> **Status:** Draft v0.1 · 2026-04-23
> **Owner:** Backend Agent
> **Scope:** gpt-image-2 + Veo 3.1 + Reconciler 的 client 設計

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
    aspect_ratio: str  # 'auto' | '1:1' | '2:3' | '3:2' (T-047 enum)
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
    source_image: bytes  # PNG — parent（Base/Alias）的圖；client 內部會同時當 first + last frame 送
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
        # gpt-image-* contract (T-042 empirical 2026-05-01):
        #   - GPT 系列不收 `response_format`、`seed`、`quality="hd"`
        #     （`response_format` / `quality=hd` 是 dall-e-2/3 only;
        #      `seed` 不在 schema)
        #   - GPT 永遠回 base64，不需要 response_format 主張
        #   - 合法 size 只有 auto / 1024x1024 / 1024x1536 / 1536x1024
        # `seed` Python 簽章保留供 caller 傳入但不夾帶到 wire body;
        # audit 透過 `generation_logs.parameters.seed` 仍記錄。
        payload = {
            "model": self.model,
            "prompt": input.prompt,
            "size": self._aspect_to_size(input.aspect_ratio),
            "n": 1,
        }
        return await self.call_with_resilience(
            self._http_post,
            "/v1/images/generations",
            payload,
        )

    async def _call_image2image(self, input):
        # OpenAI's variations / edits endpoint.
        # 多參考圖（base + N refs）：multipart field name 用 `image[]`
        # array syntax，**不可**重複 bare `image`（會 400
        # "Duplicate parameter: 'image'..."）。單一 image 走 bare
        # `image` 是合法的。T-042 empirical 2026-05-01。
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
    services = ['gpt-image-2', 'veo-3.1', 'reconciler']
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

## 4. Veo31Client（實作 VideoProvider）

### 4.1 Config

```
VEO_API_KEY=<env>                 # Gemini API key 或 Vertex AI service account
VEO_API_URL=https://generativelanguage.googleapis.com/v1beta
VEO_MODEL=veo-3.1
VEO_TIMEOUT_MS=180000             # 3 分鐘
VEO_MAX_RETRIES=2                 # 影片重試很貴
```

### 4.2 Poll 模式 + First/Last Frame 使用方式

Veo 3.1 採 long-running operation 模式：
1. POST `/models/veo-3.1:predictLongRunning` → 回 `operation_name`
2. GET `/operations/{operation_name}` 定期 poll → 完成時含 `videoUri`
3. 下載影片

**First/last frame 用法（backend 內部決定，不從 API 曝露）：**
Veo 3.1 的 `image`（first frame）與 `lastFrame` 參數都填同一張 `source_image`。這樣利用 Veo 多幀錨定機制強化 identity preservation，動作開始與結束都鎖在 parent 的造型上，避免長影片中角色走樣。使用者 / UI / API 都不暴露 first/last frame 的概念。

```python
async def generate(self, input: VideoGenerationInput) -> VideoGenerationResult:
    # 1. 送 job — first frame 與 last frame 都用 parent 的圖
    image_payload = {
        "bytesBase64Encoded": base64(input.source_image),
        "mimeType": "image/png",
    }
    payload = {
        "instances": [{
            "prompt": input.prompt,
            "image": image_payload,
            "lastFrame": image_payload,   # 故意同一張 — identity anchor
        }],
        "parameters": {
            "durationSeconds": input.duration_seconds,
            "seed": input.seed,
            "aspectRatio": "9:16",  # 或依 parent 圖片推斷
            "personGeneration": "allow_all",
        },
    }
    operation_name = await self._submit_job(payload)

    # 2. Poll 狀態
    while True:
        op = await self._poll_operation(operation_name)
        if op.done:
            if op.error:
                raise ProviderError(op.error)
            video_bytes = await self._download(op.response.videoUri)
            return VideoGenerationResult(...)

        # Push progress via Redis pubsub（Veo operation 不回 progress %，
        # 這裡用 elapsed / estimated 推算；見 task-queue.md §5.2）
        await self._publish_estimated_progress(ctx)
        await asyncio.sleep(5)
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
Veo 3.1 官方支援的 duration 區間以實際 API spec 為準；超出範圍 clamp 到最接近的合法值。

### 4.4 Retry 規則

同 gpt-image-2，但 `max_retries=2`（影片生成貴，失敗時成本高）。
特別處理：
- `INVALID_ARGUMENT` → 不 retry，回 `VALIDATION_ERROR`
- `RESOURCE_EXHAUSTED`（quota）→ 回 `MODEL_QUOTA_EXCEEDED`，不 retry

**RAI filter 例外（T-051）**

Submit-only retry 是預設原則（submit 失敗代表還沒開算錢；submit 成功後 Veo 已扣費），但 Veo 3.1 的 RAI（Responsible AI）filter 是已知的 flaky 反例：operation 順利 `done: true` 但 `response.generateVideoResponse` 出現 `raiMediaFilteredCount > 0` / `raiMediaFilteredReasons` 非空，影片被擋下沒回傳。Google 自己承認 false positive 多（`googleapis/js-genai#1272`），同 prompt+image 重試 1-2 次通常會過。

針對這條訊號專屬一條 **post-submit RAI retry budget**：

- 偵測點：`_poll_until_done` 在 `done: true` 那刻，並列 `payload.error` 檢查；命中 RAI 訊號 → 丟 `MODEL_CONTENT_FILTERED`（retryable=True，distinct from `PROMPT_CONTENT_POLICY` 的非 retryable）
- Retry budget：`VEO_RAI_MAX_RETRIES`（default 2），每次 retry 走完整 submit→poll→download（重 submit 才有意義）
- Budget 耗盡才把 `MODEL_CONTENT_FILTERED` surface 給 worker / user
- RAI 失敗**不**回饋 breaker：operation 完成代表 Veo 健康，不該因為 RAI 把無關 caller 推進 `MODEL_UNAVAILABLE`
- 每次 retry 寫一筆結構化 logger 行（`veo_rai_filter_retry` / `veo_rai_filter_retry_exhausted`），含 attempt 編號 + RAI reasons，讓 ops 看得到 false-positive rate 才能調 budget

User-facing 訊息走「目前無法生成此動作影片，系統正在自動重試」，**不**回傳 `raiMediaFilteredReasons` 字串（那是給 developer 看的，記到 `generation_log.raw_response` 即可）。

---

## 5. OpenAIReconcilerClient（Prompt Reconciler 用）

### 5.1 Config

```
OPENAI_API_KEY=<env>              # 與 gpt-image-2 共用同一把 key
RECONCILER_MODEL=gpt-5-mini
RECONCILER_TIMEOUT_MS=30000
RECONCILER_MAX_RETRIES=3
RECONCILER_MAX_TOKENS=800
```

### 5.2 主要 method

使用 OpenAI Chat Completions API 的 JSON mode：

```python
class OpenAIReconcilerClient(AIClient):
    async def reconcile(self, input: ReconcileInput) -> ReconcileOutput:
        system = self._load_system_prompt()
        user_message = self._render_user_message(input)

        # gpt-5-mini contract (T-045 empirical 2026-05-01):
        #   - `max_tokens` → `max_completion_tokens`（reasoning model
        #     family 的 wire 名）
        #   - `temperature` 只接受 default 1，不可送 0；JSON-mode 由
        #     `response_format` 強制，所以乾脆省略 temperature 欄位
        # Python attribute / config / env var (`RECONCILER_MAX_TOKENS`)
        # 仍叫 max_tokens，operator 不變；只 wire 層改名。
        response = await self.call_with_resilience(
            self._client.chat.completions.create,
            model=self.model,
            max_completion_tokens=800,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_message},
            ],
            response_format={"type": "json_object"},
        )

        data = json.loads(response.choices[0].message.content)
        return ReconcileOutput(**data, llm_latency_ms=response.latency_ms)
```

### 5.3 Degraded mode

若 OpenAI API 全掛（5 連失敗，與 gpt-image-2 circuit breaker 獨立計數）：
- Reconciler 降級為「純翻譯 + constraints append」
- 品質下降但不 block
- UX 顯示 banner：「Prompt 最佳化暫時不可用」

註：因 reconciler 與 gpt-image-2 共用 `OPENAI_API_KEY`，auth / rate-limit 錯誤可能連帶影響兩者；但業務邏輯分開，各自的 circuit breaker 獨立運作。

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
    # parameters JSONB 記錄 audit info — `seed` 是 caller 給的值
    # (gpt-image 不接受所以沒送 wire，但 audit / 日後做 retry inheritance
    # 用)；`aspect_ratio` 預設 "2:3" portrait（T-047）。
    parameters={"seed": seed, "aspect_ratio": "2:3", "image_mode": "text2image"},
    cost_units=result.cost_units,
    status="success",
    started_at=task.started_at,
    completed_at=NOW(),
    duration_ms=elapsed_ms,
)
```

**Cost units 換算：**
- gpt-image-2: 1 call = 1.0 unit (hd) / 0.5 unit (standard)
- Veo 3.1: 1 call = 10.0 units (vs image gen 昂貴 10x；first/last frame 與單幀成本相近)
- gpt-5-mini reconciler: 1 call ~ 0.01 unit（很便宜）

**UI 顯示**：乘上參考匯率（例 1 unit = $0.01 USD）顯示累計成本。

---

## 7. 密鑰管理

### 7.1 環境變數命名

```
# 核心 AI
OPENAI_API_KEY=<secret>           # gpt-image-2 + gpt-5-mini reconciler 共用
VEO_API_KEY=<secret>              # Google Veo 3.1

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

- `prompt-reconciler.md` — 使用 OpenAIReconcilerClient（gpt-5-mini）
- `task-queue.md` — Worker 內呼叫 image/video provider
- `api-shape.md` §4 錯誤格式 — 所有 provider error wrap 成 `AgentError`
- `../data/db-schema.md` GenerationLog — 成本追蹤寫入此表
