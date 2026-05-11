# Auth — Open Questions

> 待決策清單。回答前不要動實作。

---

## Q1. OAuth provider 選哪個？

| 候選 | 優點 | 缺點 | 適用情境 |
|---|---|---|---|
| **Authentik**（self-host） | 開源、自架完全控制、IdP + OAuth provider 一體 | 多一個 service 要 ops、學習曲線 | 內網作業系統符合自架精神（B3）|
| **Keycloak**（self-host） | 業界標準、生態完整 | 重、JVM | 同上 |
| **Google Identity** | 零 ops、登入體驗熟 | 綁 Google 帳號、外部依賴與 B3「內網自架」精神有張力 | 團隊都用 Google Workspace |
| **Auth.js**（library, not provider）| 嵌進 Next.js / FastAPI 自己當 provider | 還是要決定下游 storage / signing | 不算真正「provider」 |

**建議：Authentik**——對齊 B3 內網自架精神，且 Phase 1 已 docker compose（多一個容器就好）。**保留 redirect**：你若已偏好用 Google Workspace 帳號 SSO，可以選 Google。

---

## Q2. Agent 怎麼拿 token？

- **Client Credentials grant**：每個 agent 一組 `client_id` + `client_secret`，呼 token endpoint 取 access token
- **Service Account**（Google 那條路）：JSON key file
- **Token delegation**：人為自己授權 agent 持有自己的 scope

**建議：Client Credentials + Token Delegation 混合**：
- 內部 batch agent / 後台 agent → client credentials（M2M）
- 人在 UI 啟動的 agent（例：「幫我用 Claude 做一張 alias」）→ delegation flow（user 授權，agent 拿 user-scoped token）

---

## Q3. Scope 清單

最簡建議（Phase 1）：

| Scope | 對應動作 |
|---|---|
| `character:read` | GET /v1/characters/* |
| `character:write` | POST/PATCH/DELETE /v1/characters/* + /aliases/* + /motions/* |
| `task:read` | GET /v1/tasks/* |
| `task:cancel` | POST /v1/tasks/{id}/cancel |
| `usage:read` | GET /v1/usage/* |

Question：
- 要不要 `motion:write` 拆出來？（怕外部 agent 燒 Veo 額度）
- `character:copy` 要不要獨立 scope？

**建議：Phase 1 不細切**——5 個 scope 開始，使用者反饋有需求再分。

---

## Q4. JWT → OAuth migration 路徑

| 階段 | 動作 |
|---|---|
| 1. dual-stack | OAuth 上線；既有 JWT login 仍可用；新 user 走 OAuth；舊 user JWT 自然到期後改走 OAuth |
| 2. JWT login disabled | `/v1/auth/login` 回 410 Gone，要使用者改用 OAuth UI |
| 3. JWT refresh disabled | 既有 refresh token 失效，全部使用者改 OAuth |
| 4. JWT 程式碼移除 | `auth.py` 清掉 JWT-only paths |

預計 1-2 整週橫跨。

**建議：階段 1 + 2 一個 sprint，3 + 4 下一個 sprint**——分兩次降低風險。

---

## Q5. Signed URL 與 OAuth 的關係

per agent-interface open-questions Q6：**Phase 1 維持獨立 JWT 派生**。OAuth 不管 signed URL。

但要決定：
- OAuth access token 過期了，signed URL 仍可用嗎？（解耦 → 可用；耦合 → 不可用）
- 答案影響 signed URL TTL 策略

**建議：解耦**——signed URL 7 天有效（per api-shape §5.1）由 STORAGE_SIGNED_URL_SECRET 自己管；OAuth access token 短 TTL 不影響已發出的 signed URL。

---

## Q6. Refresh token 模型

OAuth refresh token 與既有 `refresh_token` table：

- **重用**：加欄位區分 token source（jwt / oauth）
- **獨立**：新表 `oauth_refresh_token`，舊表退役

**建議：重用 + 加欄位**——少一張表，migration 較簡。

---

## Q7. UI cutover 策略

新 OAuth login UI 上線時：

- 自動 redirect 到 OAuth login 嗎？
- 還是 dashboard 顯示「升級你的帳號」按鈕？
- 既有 session 要不要強制登出？

**建議：軟切換**——既有 session 跑到自然到期；新登入走 OAuth；UI 上 banner 提醒「下次登入請用 X」。

---

## Q8. MCP server 怎麼接 OAuth？

per agent-interface 設計，MCP server 接 streamable HTTP transport，agent client 帶 Bearer token：

- Token 驗證：MCP server 直接驗，還是走 backend `/v1/auth/me` proxy？
- Scope check：MCP tool 對應的 scope 在哪 enforce？

**建議：MCP server 自己驗 token + scope**——backend 不該知道 MCP 存在；MCP 是 backend 的 client，跟 frontend 平級。

---

## 決策時點

跟 agent-interface open-questions 同一輪 review（M3 收尾 / Wave G ship 後）。Auth 與 agent-interface 強耦合，必須一起定。

---

## 決策紀錄

### 2026-05-07（auth agent 視角，user confirmed，Step 2 完成）

**前置 context**：agent-interface Step 1 已完成（Round 1/2/3 全 9 條），其中 4 條 auth 問題從上游 cascade 已決（Q2 / Q3 / Q5 / Q8）。本輪只處理剩餘 4 條真決定（Q1 / Q4 / Q6 / Q7）。

| Q | Decision | 備註 |
|---|---|---|
| **Q1** | **Authentik (OSS, self-hosted) + Google Workspace 當 upstream IdP** | OAuth provider 用 Authentik OSS（免費 tier 涵蓋所有需要功能：OIDC、PRM、DCR-off、Client Credentials、custom scope）；UX 上提供「Sign in with Google」用公司 Workspace 帳號。對齊 B3 內網自架；避開 Google Identity 整套接管會踩到的三個雷（M2M JWT bearer assertion 不是 Client Credentials / 無法 host PRM / custom scope 不在 Google 框架）|
| **Q2** | ✅ 由上游決定 | agent-interface Round 2 前提：delegation（Auth Code + PKCE）+ M2M（Client Credentials）並存 |
| **Q3** | ✅ 由上游決定 | agent-interface Q5 sub-5a：5 條 scope（`character:read` / `character:write` / `task:read` / `task:cancel` / `usage:read`）+ narrow default + per-client 覆寫 |
| **Q4** | **簡化 dual-stack：1 sprint 完成** | OAuth 上線後新 login 走 OAuth；既有 JWT session 自然到期消失；`auth.py` 兩條 path 並存 ~3 週後刪 JWT path。doc 原 4 階段（dual-stack → 410 Gone → refresh disabled → code 移除）是 enterprise 場景，Phase 1 single user 過度複雜 |
| **Q5** | ✅ 由上游決定 | agent-interface Q6：signed URL 維持獨立 JWT，與 OAuth 完全解耦（`STORAGE_SIGNED_URL_SECRET` 不動、TTL 7d 不縮）|
| **Q6** | **重用既有 `refresh_token` table + 加 `token_source` 欄位**（`jwt` / `oauth`）| Round 2 5b 已決 delegated agent 無 refresh → refresh 只給 human OAuth session；既有 table 加 enum 欄位，migration 簡單 |
| **Q7** | **軟切換** | 既有 JWT session 跑到自然到期不強制登出；Login UI 直接換 OAuth flow（不放 banner，single user 不需要過渡 UX）|
| **Q8** | ✅ 由上游決定 | agent-interface Q7 sub-7a（same-process）+ sub-7c（allowlist 驗 token）：MCP server 自己驗 token + scope，與 backend `/v1/*` 走相同 token middleware |

---

## Step 2 完成（2026-05-07）

8 條 open-questions 全部拍板（4 條真決定 + 4 條從上游 cascade）。下一步切換 backend agent 視角做 Step 3：endpoint scope decorator + MCP tool 條目該怎麼長進每張 ticket 模板。
