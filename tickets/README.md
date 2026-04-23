# Character Foundry — Tickets

> 實作階段的工單系統。每張單對應一個 Claude session 可完成的範圍。

---

## 檔案組織

```
tickets/
├─ README.md        ← 本檔（工作流與慣例）
├─ _TEMPLATE.md     ← 開新單的空白模板
├─ DONE/            ← 完成的單移來這裡（git 保留）
└─ T-xxx-*.md       ← 各張工單
```

---

## 編號慣例

`T-001`, `T-002`, ... 依建立順序遞增三位數字。跨 sprint 不重置編號（保唯一性）。

完成後：
```bash
git mv tickets/T-001-scaffolding.md tickets/DONE/
```

---

## 單一張單的生命週期

```
TODO ──▶ IN_PROGRESS ──▶ DONE（移到 DONE/）
  │           │
  │           └──▶ BLOCKED（加 block 原因備註）
  └──▶ SKIPPED（加原因，罕見）
```

Status 改動時**同時更新** `STATUS.md`（所有人看得到整體進度）。

---

## Session 開工 SOP

每開新 session 要做一個 feature 時：

1. **告訴 Claude**：「我要做 T-XXX」
2. **Claude 自動做：**
   - 讀 `CLAUDE.md`（專案定位）
   - 讀 `DECISIONS.md`（核心決策）
   - 讀 `tickets/T-XXX-*.md`（本單範圍）
   - 讀單裡 **Planning refs** 列的檔案（施工細節）
   - 讀 `STATUS.md`（看前後依賴）
3. **開始寫 code。** 完成時：
   - Commit + push
   - 單 status 改 DONE
   - `git mv tickets/T-XXX-*.md tickets/DONE/`
   - 更新 `STATUS.md`

---

## 開新單的指引

不確定要不要切單時，問自己：

- 這塊能在 **1-2 小時 AI session** 做完嗎？
- 能給**明確的 acceptance criteria**（測試 / 視覺驗收）嗎？
- 跟既有單**不重複**嗎？

太大 → 拆。太小 → 合併到最相關的單。

每張單必須**指向 planning refs**，不要把 spec 細節重寫一次（會腐）。planning 是 source of truth。

---

## 估時基準（AI session）

- XS: 30 分 — 單純 CRUD、config 調整
- S: 1 小時 — 一組 endpoint / 一個元件
- M: 2 小時 — 功能一整塊（整頁 + API + 測試）
- L: 超過 2 小時 → 拆單

---

## 與 planning 的對應

| Planning 目錄 | 對應 Sprint |
|---|---|
| `planning/product/` | 所有 sprint（驗收標準參考）|
| `planning/devops/` | **Sprint 0**（基礎設施）|
| `planning/data/`、`planning/backend/` | **Sprint 0-1**（DB + auth）|
| `planning/backend/` + `planning/frontend/` | **Sprint 1-4**（功能實作）|
| `planning/ux/` | **Sprint 1-5**（視覺驗收）|

---

## 常用 commands

```bash
# 列目前進行中的單
ls tickets/ | grep -v DONE | grep -v README | grep -v TEMPLATE

# 看某張單
cat tickets/T-003-*.md

# 看已完成
ls tickets/DONE/
```
