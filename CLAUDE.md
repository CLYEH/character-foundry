# Character Foundry

## 專案簡介
一個網頁平台，讓使用者透過文字或圖片 AI 生成虛擬角色，
角色最終用於 AI 導覽員系統的虛擬形象（2D 動畫 / 未來支援 3D）。

詳細專案背景請見 `planning/project-brief.md`。

## 技術棧
- Frontend: React
- Backend: Python
- AI 模型：image generation、i2v、lip sync、image-to-3D

## 規劃資料夾
各 agent 的規劃文件存放在 `planning/` 下：

| 資料夾 | Agent 角色 | 負責範圍 |
|--------|-----------|---------|
| `planning/product/` | Product Agent | 功能範圍、使用者故事 |
| `planning/ux/` | UX Agent | 操作流程、頁面設計 |
| `planning/frontend/` | Frontend Agent | React 架構、元件設計 |
| `planning/backend/` | Backend Agent | API 設計、AI 模型串接 |
| `planning/data/` | Data Agent | 資料模型、DB schema |
| `planning/devops/` | DevOps Agent | 部署、環境、infra |

## 如何切換 Agent 視角
開新 session 時，告訴 Claude：
「請用 [agent 名稱] 的視角」，Claude 會讀取對應資料夾的 CLAUDE.md 進入角色。

例如：
- 「請用 product agent 的視角，對我做需求訪談」
- 「請用 backend agent 的視角，規劃 API」
- 「請用 data agent 的視角，設計資料模型」

## 施工原則
- planning/ 下的文件是規格書，施工時以它為準
- 有衝突或模糊時，先暫停問使用者
- 目標是完整產品，不是 MVP
