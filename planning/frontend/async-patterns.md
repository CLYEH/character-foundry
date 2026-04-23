# Character Foundry — Async Patterns

> **Status:** Draft v0.1 · 2026-04-23
> **Owner:** Frontend Agent
> **Based on:** Backend `api-shape.md` + `task-queue.md` + `ai-integration.md`

---

## 1. API Client 基礎

### 1.1 結構

```
api/
├─ client.ts                  # 核心 fetch wrapper + interceptors
├─ endpoints/                 # 按 resource 分組的呼叫函式
├─ queries/                   # TanStack Query hooks (讀)
└─ mutations/                 # TanStack Query mutations (寫)
```

### 1.2 `client.ts` 核心

```typescript
import { authStore } from '@/stores/authStore';

const BASE_URL = import.meta.env.VITE_API_BASE_URL;

export async function apiFetch<T = unknown>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const token = authStore.getState().accessToken;

  const headers = new Headers(options.headers);
  headers.set('Content-Type', 'application/json');
  if (token) headers.set('Authorization', `Bearer ${token}`);

  const res = await fetch(`${BASE_URL}${path}`, { ...options, headers });

  if (res.status === 401) {
    // Try refresh once, else redirect login
    const refreshed = await attemptTokenRefresh();
    if (refreshed) {
      // Retry original request
      return apiFetch<T>(path, options);
    }
    authStore.getState().logout();
    throw new Error('UNAUTHORIZED');
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new AgentError(body.error ?? { code: 'INTERNAL_UNEXPECTED', ... });
  }

  if (res.status === 204) return undefined as T;
  return res.json();
}
```

### 1.3 `AgentError` class

把 Backend 的 `AgentError` 回應包成 JS error：

```typescript
export class AgentError extends Error {
  code: string;
  problem: string;
  cause: string;
  fix: string;
  docsUrl?: string;
  retryable: boolean;
  requestId: string;

  constructor(raw: any) {
    super(raw.message ?? 'Unknown error');
    Object.assign(this, raw);
  }

  isCategory(prefix: string): boolean {
    return this.code.startsWith(prefix);
  }
}
```

---

## 2. TanStack Query 設定

### 2.1 QueryClient 全域設定

```typescript
// api/queryClient.ts
import { QueryClient } from '@tanstack/react-query';

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,          // 30s
      gcTime: 5 * 60_000,          // 5min
      retry: (failureCount, error) => {
        if (error instanceof AgentError && !error.retryable) return false;
        return failureCount < 2;
      },
      refetchOnWindowFocus: false,
    },
    mutations: {
      retry: 0,                     // Mutations 不自動重試
    },
  },
});
```

### 2.2 Query hook 範例

```typescript
// api/queries/useCharacter.ts
export function useCharacter(slug: string) {
  return useQuery({
    queryKey: ['characters', 'by-slug', slug],
    queryFn: () => charactersApi.getBySlug(slug),
    enabled: !!slug,
  });
}
```

### 2.3 Mutation hook 範例（+ invalidation）

```typescript
export function useCreateAlias(characterId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input) => aliasesApi.create(characterId, input),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['characters', characterId] });
      qc.invalidateQueries({ queryKey: ['characters', characterId, 'aliases'] });
      // data.task_id 會觸發 SSE subscription（見 §3）
    },
  });
}
```

---

## 3. SSE：Task Real-time Updates

### 3.1 核心 Hook：`useTaskStream`

```typescript
// hooks/useTaskStream.ts
import { fetchEventSource } from '@microsoft/fetch-event-source';

type TaskEvent = {
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';
  progress: number | null;
  message?: string;
  partial_preview_url?: string;
};

export function useTaskStream(taskId: string | null) {
  const [event, setEvent] = useState<TaskEvent | null>(null);
  const qc = useQueryClient();

  useEffect(() => {
    if (!taskId) return;
    const controller = new AbortController();

    fetchEventSource(`${BASE_URL}/v1/tasks/${taskId}/stream`, {
      signal: controller.signal,
      headers: {
        Authorization: `Bearer ${authStore.getState().accessToken}`,
      },
      onmessage: (msg) => {
        const data = JSON.parse(msg.data) as TaskEvent;
        setEvent(data);

        // 終止狀態：invalidate 相關 queries
        if (['completed', 'failed', 'cancelled'].includes(data.status)) {
          qc.invalidateQueries({ queryKey: ['tasks', taskId] });
          controller.abort();
        }
      },
      onerror: (err) => {
        console.error('SSE error', err);
        // fetch-event-source 會自動重連；這裡 log 即可
      },
      openWhenHidden: true,  // 頁面切到背景仍保持連線
    });

    return () => controller.abort();
  }, [taskId]);

  return event;
}
```

### 3.2 Polling fallback

如果 SSE 連線失敗（browser、proxy、firewall）超過 5 秒，退回 polling：

```typescript
// hooks/useTask.ts (polling version)
export function useTask(taskId: string | null) {
  return useQuery({
    queryKey: ['tasks', taskId],
    queryFn: () => tasksApi.get(taskId!),
    enabled: !!taskId,
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data) return 2000;
      if (['completed', 'failed', 'cancelled'].includes(data.status)) return false;
      return 2000;  // 每 2s poll
    },
  });
}
```

**使用策略：**
- 核心元件（CheckpointList、MotionCell、Dialog M-05）**優先 SSE**
- SSE 失敗 5s 後 `useTask` polling 啟動
- Queue position 額外需要：polling `/v1/tasks/{id}` 拿 `queue_position`（SSE stream 不會推這欄位）

### 3.3 Active task 管理

同時可能有多個 task 在跑（例：點 5 個 preset motion 一次 queue 起來）。每個 MotionCell 自己訂 SSE 就好，不需集中管理。

---

## 4. Task Cancel Flow

對應 UX wireframes §15「取消中間態」：

```typescript
async function handleCancel(taskId: string) {
  const task = queryClient.getQueryData(['tasks', taskId]) as Task;

  // Optimistic UI
  queryClient.setQueryData(['tasks', taskId], {
    ...task,
    cancel_requested: true,
    status: task.status === 'queued' ? 'cancelled' : 'running',  // 仍 running，等 server
  });

  try {
    const result = await tasksApi.cancel(taskId);
    queryClient.setQueryData(['tasks', taskId], result);

    if (result.status === 'cancelled') {
      toast.info(`已取消`);
    } else {
      // task 已跑完（來不及取消）
      toast.warning(`任務已完成（取消未成功）`);
    }
  } catch (err) {
    queryClient.setQueryData(['tasks', taskId], task);  // rollback
    toast.error(`取消失敗：${err.message}`);
  }
}
```

UI 顯示規則（已在 wireframes 定）：
- `cancel_requested === true && status === 'running'` → 顯示「取消中...」
- `status === 'cancelled'` → Toast 成功
- `status === 'completed' / 'failed'` → Toast「來不及取消」

---

## 5. Signed URL 取用與 Refresh

### 5.1 簽名 URL 特性

Backend 簽的 signed URL 預設 **1 小時**。長時間停在 P-05 Character Detail 頁的圖 / 影片會過期。

### 5.2 策略：**lazy refresh on error**

**不做**預先計算到期時間、倒數 refresh（複雜且不精確）。
**改做：**

1. 圖片 / 影片直接用 API 回傳的 signed URL `<img src="..." />`
2. 加 `onError` handler，若載入失敗：
   - 呼叫 `GET /v1/characters/{id}` 重新取得包含新 signed URL 的物件
   - React Query cache 更新 → `<img>` 重 render 使用新 URL

```typescript
// hooks/useSignedImage.ts
export function useSignedImage(src: string, onRefreshNeeded: () => void) {
  const [currentSrc, setCurrentSrc] = useState(src);
  const [hasTriedRefresh, setHasTriedRefresh] = useState(false);

  useEffect(() => { setCurrentSrc(src); setHasTriedRefresh(false); }, [src]);

  const handleError = useCallback(() => {
    if (hasTriedRefresh) return;
    setHasTriedRefresh(true);
    onRefreshNeeded();  // 觸發上層 React Query invalidate
  }, [hasTriedRefresh, onRefreshNeeded]);

  return { src: currentSrc, onError: handleError };
}
```

### 5.3 元件使用

```tsx
function BaseImage({ base }: { base: Base }) {
  const qc = useQueryClient();
  const { src, onError } = useSignedImage(
    base.image_url,
    () => qc.invalidateQueries({ queryKey: ['characters', base.character_id] }),
  );
  return <img src={src} onError={onError} alt="base" loading="lazy" />;
}
```

---

## 6. Auth Token Refresh

### 6.1 Access token 短命（15min）+ Refresh token（30 天）

Interceptor 在 `client.ts`（已在 §1.2 提到）遇到 401 時：

```typescript
async function attemptTokenRefresh(): Promise<boolean> {
  const refreshToken = authStore.getState().refreshToken;
  if (!refreshToken) return false;

  try {
    const res = await fetch(`${BASE_URL}/v1/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    if (!res.ok) return false;

    const { access_token, expires_in } = await res.json();
    authStore.getState().updateAccessToken(access_token, expires_in);
    return true;
  } catch {
    return false;
  }
}
```

### 6.2 防止同時多個 refresh

用 module-level promise 確保同時間只有一次 refresh in-flight：

```typescript
let refreshPromise: Promise<boolean> | null = null;

async function attemptTokenRefresh() {
  if (refreshPromise) return refreshPromise;
  refreshPromise = doRefresh();
  const result = await refreshPromise;
  refreshPromise = null;
  return result;
}
```

### 6.3 Persist

`authStore` 用 Zustand 的 `persist` middleware 存到 `localStorage`：

```typescript
import { persist } from 'zustand/middleware';

export const authStore = create(
  persist(
    (set) => ({ ... }),
    { name: 'cf-auth' }
  )
);
```

---

## 7. Error Handling Pipeline

對應 UX wireframes §16 三層錯誤模型：

### 7.1 Layer 1 — Form inline

Backend 回 `VALIDATION_*` / `CONFLICT_*` → React Hook Form 設 error：

```typescript
const form = useForm({ resolver: zodResolver(schema) });

const { mutate } = useCreateCharacter({
  onError: (err: AgentError) => {
    if (err.isCategory('VALIDATION_') || err.isCategory('CONFLICT_')) {
      form.setError('name', { type: 'server', message: err.message });
    } else {
      toast.error(err.message);
    }
  },
});
```

### 7.2 Layer 2 — Toast（async task 失敗）

用 Sonner：

```typescript
import { toast } from 'sonner';

toast.error(err.message, {
  description: err.problem,
  action: err.retryable ? { label: '重試', onClick: () => retry() } : undefined,
  duration: 8000,
});
```

展開詳細：
```tsx
<ToastWithExpand
  title="生成失敗"
  summary={err.message}
  details={{
    problem: err.problem,
    cause: err.cause,
    fix: err.fix,
    requestId: err.request_id,
  }}
/>
```

### 7.3 Layer 3 — Page-level

**ErrorBoundary**（React 19 `<ErrorBoundary>` 或自建）：

```tsx
<ErrorBoundary fallback={<ErrorPage />}>
  <App />
</ErrorBoundary>
```

**401** → 由 `client.ts` 的 interceptor 處理，redirect `/login`
**404** → React Router 內建 `$.tsx` 路由
**500 / network** → ErrorBoundary 捕捉 AgentError / Fetch errors

---

## 8. Degraded Mode Banner

Backend 在 circuit breaker open / reconciler fallback 時會在 `/v1/meta` 回應加 `degraded_services` 陣列：

```typescript
// hooks/useDegradedMode.ts
export function useDegradedMode() {
  const { data } = useQuery({
    queryKey: ['meta'],
    queryFn: () => metaApi.get(),
    refetchInterval: 60_000,  // 每分鐘 check
  });
  return data?.degraded_services ?? [];
}

// 在 AppLayout 顯示 banner
function DegradedBanner() {
  const degraded = useDegradedMode();
  if (degraded.length === 0) return null;

  const messages = degraded.map(s => SERVICE_MESSAGES[s]);
  return (
    <Alert variant="warning">
      ⚠ {messages.join('、')}
    </Alert>
  );
}
```

**注意：** `/v1/meta` 尚未在 api-shape.md 定義 `degraded_services`。這需要 Backend 補一個 field（或新 endpoint）。記為 **F→B 待確認**。

---

## 9. 長任務 optimistic UI

### 9.1 建 Alias（F-10）

使用者按「生成 Alias」的體驗：

```
t=0     按下按鈕
t=0.1s  Mutation 送出 → Backend 回 { task_id, alias_id }
t=0.1s  Frontend:
        - 關掉建立表單 (UX 設計：這時就離開 P-06 回 P-05)
        - 在 P-05 的 aliases 清單加一筆「生成中」佔位 (optimistic insert)
        - 啟動 useTaskStream(task_id)
t=5s    SSE 回 progress: 0.25 → 更新佔位 UI
t=20s   SSE 回 status: 'completed' + result
        - Invalidate ['characters', id] query → 重新 fetch
        - 該 alias 從佔位變正式
        - 可選：Toast「紅旗袍版 建立完成」
```

### 9.2 Optimistic insert

```typescript
const mutation = useMutation({
  mutationFn: (input) => aliasesApi.create(characterId, input),
  onMutate: async (input) => {
    // Cancel in-flight queries
    await qc.cancelQueries({ queryKey: ['characters', characterId] });
    const previous = qc.getQueryData(['characters', characterId]);

    // Optimistic
    qc.setQueryData(['characters', characterId], (old: Character) => ({
      ...old,
      aliases: [
        ...old.aliases,
        {
          id: `temp-${Date.now()}`,
          name: input.name,
          _optimistic: true,  // UI 知道是佔位
          _status: 'queued',
        },
      ],
    }));

    return { previous };
  },
  onError: (_err, _input, ctx) => {
    qc.setQueryData(['characters', characterId], ctx.previous);
  },
  onSettled: () => {
    qc.invalidateQueries({ queryKey: ['characters', characterId] });
  },
});
```

---

## 10. 給 Backend 的回饋（F→B 待補）

| # | 議題 |
|---|---|
| FB-1 | `/v1/meta` 需要加 `degraded_services` 欄位（§8）|
| FB-2 | SSE event 要不要包含 `queue_position` 更新？（現在只能 polling 查）|
| FB-3 | Signed URL 到期時，error response format？backend 目前回 403 AgentError 即可，但建議用特定 `STORAGE_URL_EXPIRED` code，frontend 才好判斷是 refresh 而不是真的沒權限 |
| FB-4 | Task cancel 的 response body：明確回應 final status（completed / failed / cancelled），UI 才好 message 顯示 |

Backend 可在下一個 iteration 回應這幾題（小改動）。

---

## 11. 關聯文件

- `architecture.md` — Tech stack + folder structure
- `component-map.md` — 元件對應
- `../backend/api-shape.md` — 核心 API 合約
- `../backend/task-queue.md` — Async 語義來源
- `../backend/ai-integration.md` — Error code 分類
- `../ux/wireframes.md` — UI 狀態對照
