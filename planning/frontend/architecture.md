# Character Foundry — Frontend Architecture

> **Status:** Draft v0.1 · 2026-04-23
> **Owner:** Frontend Agent
> **Based on:** UX `wireframes.md` + Backend `api-shape.md`

---

## 1. 技術棧（F1-F9 定案）

| # | 選項 | 決定 | 版本（Phase 1） |
|---|---|---|---|
| F1 | Meta-framework | **Vite + React Router v7** | React 19 + Vite 6 + RR v7 |
| F2 | Component library | **shadcn/ui + Tailwind CSS** | Tailwind 4.x, Radix primitives |
| F3 | State management | **Zustand + TanStack Query** | Zustand 5, TQ v5 |
| F4 | Form management | **React Hook Form + Zod** | RHF v7, Zod 3 |
| F5 | SSE client | **@microsoft/fetch-event-source** | latest |
| F6 | Canvas (inpaint) | **react-konva** | latest |
| F7 | Icons | **Lucide React** | latest |
| F8 | Testing | **Vitest + Testing Library + Playwright** | latest |
| F9 | TypeScript / API types | **strict mode + openapi-typescript** | TS 5.x |

額外工具：
- **Prettier + ESLint**（Anthropic / Airbnb 風格 base）
- **Husky + lint-staged** pre-commit
- **pnpm** 作為 package manager（disk space 省、monorepo-friendly 未來）

---

## 2. 專案結構

```
character-foundry-web/
├─ public/
│  └─ favicon, logo assets
├─ src/
│  ├─ main.tsx                       # Entry
│  ├─ App.tsx                        # Router root
│  │
│  ├─ routes/                        # React Router v7 routes
│  │  ├─ _auth.tsx                   # Auth layout (login only)
│  │  ├─ _layout.tsx                 # Main layout (TopNav + outlet)
│  │  ├─ login.tsx
│  │  ├─ _index.tsx                  # Dashboard
│  │  ├─ characters.new.tsx          # P-03
│  │  ├─ characters.new.session.$sessionId.tsx  # P-04
│  │  ├─ characters.$slug.tsx        # P-05
│  │  ├─ characters.$slug.aliases.new.tsx       # P-06
│  │  ├─ usage.tsx                   # P-07
│  │  ├─ settings.tsx                # P-08
│  │  └─ $.tsx                       # 404
│  │
│  ├─ components/
│  │  ├─ ui/                         # shadcn/ui primitives (auto-generated)
│  │  │  ├─ button.tsx
│  │  │  ├─ input.tsx
│  │  │  ├─ dialog.tsx
│  │  │  ├─ toast.tsx
│  │  │  └─ ...
│  │  ├─ composite/                  # Application-specific composites
│  │  │  ├─ TopNav/
│  │  │  ├─ CharacterCard/
│  │  │  ├─ CheckpointList/
│  │  │  ├─ MotionRow/
│  │  │  ├─ AliasRow/
│  │  │  ├─ InpaintCanvas/
│  │  │  ├─ InputModeSelector/
│  │  │  ├─ PromptAdvancedView/
│  │  │  ├─ UsageWidget/
│  │  │  └─ TaskStatus/              # Queue #/progress/spinner unified
│  │  └─ layout/
│  │     ├─ AuthLayout.tsx
│  │     └─ AppLayout.tsx
│  │
│  ├─ hooks/
│  │  ├─ useAuth.ts                  # Zustand auth store hook
│  │  ├─ useTaskStream.ts            # SSE subscription hook
│  │  ├─ useSignedUrl.ts             # Signed URL refresh
│  │  ├─ useClipboardCopy.ts
│  │  └─ useInpaintCanvas.ts
│  │
│  ├─ stores/                        # Zustand stores
│  │  ├─ authStore.ts
│  │  ├─ toastStore.ts
│  │  ├─ uiStore.ts                  # sidebar state, theme, etc.
│  │  └─ creationStore.ts            # P-04 creation session local state
│  │
│  ├─ api/
│  │  ├─ client.ts                   # fetch wrapper + interceptors
│  │  ├─ endpoints/                  # Thin wrappers grouped by resource
│  │  │  ├─ auth.ts
│  │  │  ├─ characters.ts
│  │  │  ├─ creationSessions.ts
│  │  │  ├─ aliases.ts
│  │  │  ├─ motions.ts
│  │  │  ├─ tasks.ts
│  │  │  ├─ usage.ts
│  │  │  └─ meta.ts
│  │  ├─ queries/                    # TanStack Query hooks
│  │  │  ├─ useCharacters.ts
│  │  │  ├─ useCharacter.ts
│  │  │  ├─ useTask.ts
│  │  │  └─ ...
│  │  ├─ mutations/                  # TanStack Query mutations
│  │  │  ├─ useCreateCharacter.ts
│  │  │  ├─ useCreateAlias.ts
│  │  │  └─ ...
│  │  └─ generated/
│  │     └─ openapi-types.ts         # Auto-generated from backend OpenAPI
│  │
│  ├─ lib/
│  │  ├─ format.ts                   # date / currency / size formatters
│  │  ├─ validators.ts               # Zod schemas
│  │  ├─ constants.ts                # preset motion types, etc.
│  │  └─ cn.ts                       # clsx + tailwind-merge helper
│  │
│  ├─ types/
│  │  ├─ task.ts                     # Task DTO type extensions
│  │  ├─ character.ts
│  │  └─ index.ts
│  │
│  └─ styles/
│     ├─ globals.css                 # Tailwind + CSS vars
│     └─ themes.css                  # Light/dark/custom themes
│
├─ tests/
│  ├─ e2e/                           # Playwright
│  │  ├─ auth.spec.ts
│  │  ├─ create-character.spec.ts
│  │  └─ alias-create.spec.ts
│  └─ unit/                          # Vitest (co-located with src 也可)
│
├─ index.html
├─ vite.config.ts
├─ tailwind.config.ts
├─ tsconfig.json
├─ .env.example
└─ package.json
```

**目錄原則：**
- `routes/` = 路由（一頁一檔）
- `components/ui/` = shadcn 原生元件（`npx shadcn@latest add` 產生）
- `components/composite/` = 應用級元件（資料夾包含 component.tsx + index.ts）
- `api/endpoints/` = 薄包裝 fetch（只做 HTTP，不做狀態）
- `api/queries/` / `api/mutations/` = TanStack Query hooks（狀態、cache、retry）
- `stores/` = Zustand（全域 client state）

---

## 3. Routing 清單

| Path | Route file | Auth | Component |
|---|---|---|---|
| `/login` | `login.tsx` | ✗ | LoginPage |
| `/` | `_index.tsx` | ✓ | DashboardPage |
| `/characters/new` | `characters.new.tsx` | ✓ | NewCharacterPage |
| `/characters/new/session/{id}` | `characters.new.session.$sessionId.tsx` | ✓ | CreationSessionPage |
| `/characters/{slug}` | `characters.$slug.tsx` | ✓ | CharacterDetailPage |
| `/characters/{slug}/aliases/new` | `characters.$slug.aliases.new.tsx` | ✓ | AliasEditPage |
| `/usage` | `usage.tsx` | ✓ | UsagePage |
| `/settings` | `settings.tsx` | ✓ | SettingsPage |
| `/*` | `$.tsx` | - | NotFoundPage |

**Protected route 處理：** `_layout.tsx` 在 loader 階段檢查 auth token，無效則 redirect 到 `/login?redirect_back={current}`。

**登入後 redirect：** 讀 `?redirect_back` query，否則跳 `/`。

---

## 4. State 架構

### 4.1 Server state（TanStack Query）

所有**來自後端的資料**走 TQ：
- Character list / detail
- Alias list / detail
- Motion list / detail
- Task status
- Usage data
- Meta / platform info

**Query key 命名約定：**

```typescript
// 資源 + 識別 + 參數
['characters']                         // list
['characters', { owner: 'me' }]
['characters', characterId]            // detail
['characters', characterId, 'aliases'] // sub-resource
['tasks', taskId]
['usage', 'me', { period: 'current' }]
```

**Cache invalidation 規則：**
- Mutation 成功後 invalidate 相關 query key
- 例：建 Alias → `qc.invalidateQueries(['characters', id, 'aliases'])` + `qc.invalidateQueries(['characters', id])` (motions_summary 會變)

### 4.2 Client state（Zustand）

**authStore** — JWT、user info
```typescript
{
  accessToken: string | null
  refreshToken: string | null
  user: User | null
  login(token, refresh, user): void
  logout(): void
}
```

**toastStore** — Toast queue（M-01 ~ M-05 的底層）
```typescript
{
  toasts: Toast[]
  show(toast): void
  dismiss(id): void
}
```

**uiStore** — 全域 UI 偏好
```typescript
{
  sidebarCollapsed: boolean
  theme: 'light' | 'dark' | 'system'
  toggleSidebar(): void
}
```

**creationStore** — P-04 本地狀態（不需 persist 到後端的部分）
```typescript
{
  selectedCheckpointId: string | null
  menuSelections: Record<string, string>
  freeformNote: string
  referenceImageIds: string[]
  selectCheckpoint(id): void
  updateMenu(key, value): void
  reset(): void
}
```

### 4.3 決策樹：放哪個 store

```
是從後端來的？
├─ Yes → TanStack Query
└─ No → 要跨頁面共用？
        ├─ Yes → Zustand
        └─ No → React 本地 state (useState / useReducer)
```

---

## 5. 環境變數

```
# .env.local
VITE_API_BASE_URL=http://localhost:8000
VITE_STORAGE_BASE_URL=http://localhost:8000
VITE_APP_VERSION=0.1.0
```

Prod `.env.production`：
```
VITE_API_BASE_URL=https://character-foundry.internal/api
VITE_STORAGE_BASE_URL=https://character-foundry.internal
```

**注意：** Vite 把 `VITE_*` 前綴的變數 inline 到 bundle，**不要**放任何密鑰在這裡。JWT 在使用者登入後拿到，存在 Zustand + localStorage（見 auth flow）。

---

## 6. Build & Dev

### 6.1 `vite.config.ts` 重點

- React plugin
- Path alias `@/` → `./src/`
- Proxy dev：`/api` → `http://localhost:8000`（避免 CORS 折騰）
- Code splitting：route-level（React Router v7 預設）+ heavy deps lazy (`react-konva` chunk)

### 6.2 Dev server

```bash
pnpm dev       # Vite dev server, HMR
pnpm typegen   # 從 backend OpenAPI 產 TS types
pnpm test      # Vitest watch
pnpm e2e       # Playwright (需 backend running)
pnpm lint      # ESLint + Prettier check
```

### 6.3 OpenAPI type 生成

```bash
# package.json script
"typegen": "openapi-typescript http://localhost:8000/openapi.json -o src/api/generated/openapi-types.ts"
```

開發時 backend 啟動後跑 `pnpm typegen` 更新 types。CI 跑 `typegen && build` 確保 Frontend 對應最新 API。

---

## 7. 測試策略

### 7.1 Vitest + Testing Library（單元 / 元件）

**一定要測：**
- 表單驗證邏輯（login, Character name）
- InpaintCanvas 的 mask 輸出格式
- PromptAdvancedView 的 removed_segments 渲染
- Error → UI layer 的 mapping 函式
- TaskStatus 元件的各種狀態渲染

**不測：**
- shadcn 原生元件（已經有自己的測試）
- 純 pass-through 的 wrapper

### 7.2 Playwright（E2E）

**一定要測的 flow：**
- Login → Dashboard → Create Character（template 模式完整到 Base 確立）
- Add Alias（三合一輸入至少測純文字版）
- Generate Motion（點預設動作按鈕 → task 完成）
- Download ZIP
- Copy 他人 Character

**E2E 需要 Backend + DB 真的跑**（用 seed data）。

### 7.3 Coverage 目標

- Unit：70%+
- E2E：5 個核心 flow 必過

---

## 8. 效能目標

| 指標 | 目標 |
|---|---|
| Initial JS bundle（gzip）| < 300 KB |
| Route chunks | < 100 KB each |
| Time to Interactive（P-02）| < 2s on mid-range laptop |
| Lighthouse Performance | 90+ |
| React render | 60fps on canvas interaction |

**達成策略：**
- Route-level code splitting（RR v7 預設）
- `react-konva` lazy load（僅 P-06 用）
- Images：`<img loading="lazy">`，用 signed URL 的 thumbnail（backend 提供縮圖）
- Fonts：self-host + `font-display: swap`
- 不引入 moment.js / lodash 類大 lib（用 date-fns / radash 替代）

---

## 9. Accessibility

shadcn/ui 基於 Radix，a11y 底子好。我們要守的：

- 所有互動元素 keyboard accessible
- Focus ring 明顯
- Form field 有 `<label>` 或 `aria-label`
- 影片有鍵盤 play/pause
- Color contrast WCAG AA（Design token 定色時注意）
- Screen reader 友好的 loading 狀態（`aria-live="polite"` 給 Toast）

---

## 10. 關聯文件

- `component-map.md` — 18 元件對應到 library primitives
- `async-patterns.md` — SSE、polling、cancel、signed URL refresh
- `../ux/wireframes.md` — 視覺 layout 參考
- `../backend/api-shape.md` — API 合約
- `../backend/task-queue.md` — async 語義
