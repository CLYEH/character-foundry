# Character Foundry — Component Map

> **Status:** Draft v0.1 · 2026-04-23
> **Owner:** Frontend Agent
> **Based on:** UX `wireframes.md` §17 18-component inventory

---

## 1. 對應原則

| 類型 | 目的 | 放哪 |
|---|---|---|
| **Primitive**（原生 shadcn）| 基礎元件，不客製 | `components/ui/` |
| **Wrapper**（薄包裝 shadcn）| 加專案慣例（例：icon 約定、error state 處理）| `components/ui/` 或 `components/composite/` |
| **Composite**（應用級組合）| 業務邏輯 + 多個 primitive 組成 | `components/composite/` |
| **Page**（路由級）| 一個 route 的 root 元件 | `routes/` |

---

## 2. Primitive Components（shadcn/ui 直接使用）

這些透過 `npx shadcn@latest add <name>` 拉進 `components/ui/`：

| 元件 | shadcn name | 用在哪 |
|---|---|---|
| Button | button | 全站 |
| Input | input | 表單 |
| Textarea | textarea | 自由補述、動作描述 |
| Select | select | 選單（性別、眼型、髮型等）|
| Checkbox | checkbox | 輸入方式 toggle（P-06）|
| Radio Group | radio-group | 單選（備用）|
| Card | card | Character / Alias / Motion 卡片 |
| Dialog | dialog | 所有 Modal (M-01 ~ M-05) 的底 |
| Popover | popover | `⋯` 選單 |
| Dropdown Menu | dropdown-menu | User menu, card actions |
| Tabs | tabs | 備用（Phase 1 可能不用）|
| Toast (Sonner) | sonner | 通知 |
| Progress | progress | Task progress bar |
| Skeleton | skeleton | Loading 骨架 |
| Separator | separator | 分隔 |
| Tooltip | tooltip | Hover 說明（non-owner 的禁用按鈕等）|
| Avatar | avatar | User menu 頭像 |
| Badge | badge | 狀態標籤（「生成中」「已取消」）|
| Alert | alert | Banner 警示 |
| Sheet | sheet | 備用（若未來側邊欄）|

**注意：** Sonner（toast）取代 shadcn 舊的 useToast。新版預設。

---

## 3. Wrapper Components

極薄客製，加上專案慣例：

### 3.1 `AppButton`（基於 `Button`）
- 統一 icon 使用方式（left icon / right icon props）
- 統一 loading state（`isLoading` prop → 自動變 disabled + spinner）
- 約定 variants：`primary` / `secondary` / `danger` / `ghost` / `icon-only`

### 3.2 `FormField`（基於 `Input` / `Textarea` / `Select`）
- 統一 label / error / hint 三件組樣式
- 整合 `react-hook-form` 的 Controller
- 自動處理 `aria-invalid` 與 error message 顯示

### 3.3 `ConfirmDialog`（基於 `Dialog`）
- 統一「確認 / 取消」按鈕 layout
- Props：`title, description, confirmText, cancelText, onConfirm, variant (normal/danger)`
- 用在 M-03 Copy 確認、M-04 刪除 / 還原

---

## 4. Composite Components（18 個 + 衍生）

對應 UX `wireframes.md §17`，每個元件都是獨立資料夾：

```
components/composite/
├─ TopNav/
│  ├─ TopNav.tsx
│  ├─ TopNav.test.tsx
│  └─ index.ts
└─ ...
```

### 4.1 `TopNav`
**用在：** 所有 `/`, `/characters/*`, `/usage`, `/settings`
**構成：** Logo + Search + UsageWidget + UserMenu
**子元件：** `SearchInput`, `UsageWidget`, `UserMenu`
**狀態：** 讀 `authStore`, `uiStore.sidebarCollapsed`

### 4.2 `CharacterGrid` + `CharacterCard`
**用在：** P-02 Dashboard
**CharacterCard props：**
```typescript
{
  character: Character  // 從 api/types 來
  viewMode: 'owner' | 'viewer'  // 控制顯示 [⋯] or [Copy]
  onCopy?: () => void
  onDelete?: () => void
}
```
**互動：** 點卡片 nav 到 `/characters/{slug}`；`⋯` 彈 DropdownMenu；[Copy] 開 M-03

### 4.3 `CheckpointList` + `CheckpointCard`
**用在：** P-04 右欄
**關鍵：** 每個 card 有 5 種狀態（queued / running / completed / failed / cancelled），用統一的 `TaskStatusBadge` 子元件
**衍生：** `CheckpointDetailLightbox`（點 checkpoint 開大圖 + prompt 資訊）

### 4.4 `MotionRow` + `MotionCell`
**用在：** P-05 每個 Base / Alias 卡片底下
**MotionRow props：**
```typescript
{
  parentType: 'base' | 'alias'
  parentId: string
  motions: Motion[]
  isOwner: boolean
  onGeneratePreset: (type: PresetMotionType) => void
  onAddCustom: () => void
}
```
**MotionCell 5 種狀態：** empty (`+`) / queued / running / completed (`🎬`) / failed (`!`)
**互動：** 點 empty → 觸發生成；點 completed → lightbox 播放；點 failed → 顯示錯誤 + 重試

### 4.5 `AliasRow`
**用在：** P-05
**構成：** Alias 卡片 + 名稱 + MotionRow
**可見性：** Owner 顯示 `[編輯名稱] [刪除]`；viewer 只顯示不可操作

### 4.6 `InpaintCanvas` ⭐
**用在：** P-06 Alias 編輯頁
**依賴：** `react-konva`
**功能：**
- 載入 Base 圖作為底層 `<Image>`
- Drawable layer 疊在上面
- 支援筆刷 / 橡皮擦模式
- 支援縮放 / 平移（可選）
- 輸出 mask 為 PNG bitmap（符合 Backend 期望）
- 顯示 mask 覆蓋比例（% of base image）

**Props：**
```typescript
{
  baseImageUrl: string
  enabled: boolean
  onMaskChange: (maskPng: Blob | null, coveragePercent: number) => void
}
```

### 4.7 `InputModeSelector`
**用在：** P-03
**構成：** 兩個大卡片（Template / Reference）
**互動：** 點擊 → 帶入對應 mode 往下（不直接送 API）

### 4.8 `PromptAdvancedView`
**用在：** M-01 Modal
**資料來源：** `POST /v1/prompt/preview` 回應
**顯示內容：**
- 平台 constraints 清單
- 原文 vs 英譯
- 選單 fragments
- Removed segments (with reason)
- Final prompt（可 copy to clipboard）

### 4.9 `UsageWidget` / `UsageDashboard`
**Widget**（小，用在 TopNav）：
```typescript
{
  currentMonthCostUsd: number
  softLimitUsd: number  // 預設 100
}
```
Progress bar（到 80% 警色、100% 紅但持續顯示）+ $42.5 / $100。

**Dashboard**（大，用在 P-07）：完整圖表 + 表格 + 下載 CSV。

### 4.10 `TaskStatus` / `TaskStatusBadge` / `TaskProgress`
統一 async task 狀態呈現（wireframes 裡散落在 checkpoint / motion / M-05）：

```typescript
type TaskUI =
  | { state: 'queued'; position: number | null }  // #3 in queue
  | { state: 'running'; progress: number | null; estimated: number }
  | { state: 'cancelling' }
  | { state: 'completed' }
  | { state: 'failed'; error: AgentError }
  | { state: 'cancelled' }
```

一個 component 服務全部，不同父元件傳不同 size / variant：
- `TaskStatusBadge`（inline、小）
- `TaskProgressLine`（進度條、中）
- `TaskProgressDialog`（Modal M-05 的 progress）

### 4.11 `ErrorBoundary` / `ErrorPage`
- `ErrorBoundary` 捕 render 錯誤，fallback UI
- `ErrorPage`（Layer 3）：連線失敗、404、401 redirect 前暫留

---

## 5. Page Components（routes/ 下）

每頁面是獨立檔案。主要構成：

| Page | 用的 Composite | 資料來源 |
|---|---|---|
| LoginPage | AuthLayout + FormField + AppButton | `POST /auth/login` mutation |
| DashboardPage | TopNav + CharacterGrid | `useCharacters` query |
| NewCharacterPage | TopNav + InputModeSelector + FormField | `POST /characters` mutation |
| CreationSessionPage | TopNav + 左輸入區 + CheckpointList | `useCreationSession` query + SSE |
| CharacterDetailPage | TopNav + Character header + BaseCard + AliasRow×N | `useCharacter` query |
| AliasEditPage | TopNav + InpaintCanvas + 右輸入區 | `POST /characters/{id}/aliases` mutation |
| UsagePage | TopNav + UsageDashboard | `useUsage` query |
| SettingsPage | TopNav + FormField groups | `useUser` + user update mutation |
| NotFoundPage | ErrorPage(404) | - |

---

## 6. Layout Components

### 6.1 `AuthLayout`
- 用在 `/login`
- 無 TopNav，置中 form

### 6.2 `AppLayout`
- 用在所有其他路由
- 包 TopNav + Outlet
- 加 global banner 區（degraded mode）
- 加 Toast container（Sonner `<Toaster />`）

---

## 7. 依賴關係圖

```
Pages
  └── use Composite components
         └── use Wrappers (AppButton, FormField, ConfirmDialog)
                └── use shadcn Primitives (Button, Dialog, ...)
                       └── Radix + Tailwind CSS variables
```

**原則：** 禁止 Page 直接用 primitive 跳過 composite（強迫 DRY，改視覺時一處改全部改）。

---

## 8. 實作順序建議（Phase 1 開發階段）

**Sprint 1（基礎）：**
1. shadcn primitives 全部 add 進來
2. AppButton / FormField / ConfirmDialog wrappers
3. AppLayout / AuthLayout
4. TopNav（骨架，不串資料）
5. authStore + login page + API client 基礎

**Sprint 2（Dashboard + 建立）：**
6. useCharacters query + CharacterGrid + CharacterCard
7. NewCharacterPage + InputModeSelector
8. CreationSessionPage + CheckpointList + CheckpointCard
9. TaskStatus 一族 + SSE subscription hook

**Sprint 3（角色操作）：**
10. CharacterDetailPage + BaseCard + AliasRow + MotionRow + MotionCell
11. AliasEditPage + InpaintCanvas（react-konva 深水區）
12. Custom motion modal (M-02)
13. Copy modal (M-03)

**Sprint 4（其他）：**
14. ZIP 下載 (M-05)
15. UsageWidget + UsagePage
16. SettingsPage
17. Delete / restore (M-04)
18. PromptAdvancedView (M-01)
19. 所有 empty / error 狀態收尾

**Sprint 5（打磨）：**
20. E2E tests
21. Performance optimization
22. A11y audit

---

## 9. 關聯文件

- `architecture.md` — Stack + folder structure
- `async-patterns.md` — SSE、polling、signed URL、cancel flow
- `../ux/wireframes.md` — 視覺對照
- `../backend/api-shape.md` — API 合約
