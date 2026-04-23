# 平台目標

讓使用者透過生成式 AI 從零建構專屬客製化角色（影像），並建置為可重複使用的 asset。

## 平台原則

- **人機雙介面**：除了讓真人透過 UI 操作之外，平台也必須是 **agent-friendly** 的。
- **可供 agent 使用的 skill**：平台的每項能力（角色生成、alias 編修、i2v、image-to-3D、asset 管理…）都要能被包裝成 agent 可呼叫的 skill，讓 agent 能程式化地完成整條工作流程。
- **設計意涵**：
  - API 設計需要自描述、參數清晰、錯誤訊息對 agent 友善
  - 非同步任務狀態要容易 poll / webhook
  - Asset / alias / motion / 3D model 的識別與查詢要穩定（適合 agent 組合使用）
  - 優先考慮 MCP server 或類似協定，讓 agent 能直接掛接

## 核心概念

- **Asset（角色本體）**：使用者生成的角色影像，是平台上的基礎單位。
- **Alias（角色變體）**：以 asset 為基礎，進一步編修服飾、配件、打扮等外觀細節，產生同一角色的不同樣貌。
- **Motion（動作影片）**：asset 或 alias 可透過 image-to-video 生成不同動作的影片。
- **3D Model（立體模型）**：asset 或 alias 可透過 image-to-3D 轉為 3D 模型，供後續立體形式的應用。

## 流程與模型

1. 文字或圖片輸入 → 生成角色 asset
   - 模型：**gpt-image-2**（text-to-image）
2. Asset → 編修外觀 → 產生 alias
   - 模型：**gpt-image-2**（image-to-image / text-to-image / inpaint 視編修情境選用）
3. Asset 或 alias → i2v → 動作影片
   - 模型：**Seedance 2.0**
4. Asset 或 alias → image-to-3D → 3D 模型
   - 模型：**待定**（候選：Trellis、Hunyuan）
