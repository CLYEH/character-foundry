# T-076: Authentik flow interface XHR is CORS-blocked on the `:5173` dev origin — flow stuck at "Loading…"

**Status:** TODO
**Sprint:** Backlog（post-3.5a；dev-topology wall — T-075 CDP 驗證 reveal，同 T-070 family）
**Est:** S
**Depends on:** T-075（encoding fix 先 land；本單疊在它之上）
**Related:** T-073（引入「走 flow interface」這條路）、T-070（dev proxy topology，本單是它的延伸）、T-075（encoding fix）

---

## Scope

修「SPA 走 `cf-google-init` flow 時，Authentik flow **interface** 的 bootstrap XHR 在 `:5173` dev origin 被 CORS 擋掉、整頁卡在 `Loading…`」。

### 怎麼發現的（2026-05-15，T-075 CDP 驗證）

T-075 把 `buildSourceInitUrl` 的 encoding 修對後（network log 確認 SPA 已導到正確的 `/oauth/if/flow/cf-google-init/?next=<authorize URL>`、flow interface 也發出格式正確的 `flows/executor/cf-google-init/?query=next%3D%252Foauth...` 呼叫），flow interface 仍卡在 `Loading…`。CDP console log：

```
Access to fetch at 'http://localhost/oauth/api/v3/core/brands/current/'
  from origin 'http://localhost:5173' has been blocked by CORS policy:
  Response to preflight request doesn't pass access control check
Access to fetch at 'http://localhost/oauth/api/v3/root/config/' ... blocked by CORS
Access to fetch at 'http://localhost/oauth/api/v3/flows/executor/cf-google-init/?query=...' ... blocked by CORS
```

### Root cause

- SPA 在 `http://localhost:5173`。點「使用 Google 登入」→ 導到 `http://localhost:5173/oauth/if/flow/cf-google-init/?next=...`（vite proxy `/oauth/` → nginx → Authentik，**flow interface HTML 載得起來**）。
- 但 flow interface 的前端用 Authentik 注入的 `base_url` 發 API XHR。`base_url` 由 `core/views/interface.py` 的 `self.request.build_absolute_uri(...)` 算出來 —— 而 T-070 讓 nginx 用 `$host`（去 port）→ Authentik 看到 `Host: localhost`（無 `:5173`）→ `base_url = http://localhost/oauth/`。
- 所以 flow interface 的 XHR 打 **絕對 URL** `http://localhost/oauth/api/v3/...`（port 80），跟它所在的 origin `http://localhost:5173` 跨來源 → preflight → Authentik 不回 `Access-Control-Allow-Origin: http://localhost:5173` → 全部被擋 → interface 卡在 `Loading…`、RedirectStage 永遠沒機會跑。

**這是 dev-`:5173`-only。** Prod / CI e2e 整套同 origin（`nginx:80`），flow interface + API 同源、無 CORS —— 所以 e2e 綠、prod 沒事。是 T-073「走 flow interface」這條路撞上 T-070 dev-proxy topology 的後果。

**In scope:**
1. 讓 dev `:5173` 下 flow interface 的 bootstrap XHR（`core/brands/current`、`root/config`、`flows/executor/...`）不被 CORS 擋。
2. 不破壞 T-070 已修好的東西（Google `redirect_uri` 要維持 `http://localhost/...`、proxy hop 維持通）。
3. 不破壞 prod / CI e2e 的同源路徑。

**Not in scope:**
- T-075 的 encoding fix（已 land / 進行中）。
- T-074 的 open-redirect hardening。

---

## Planning refs（開工前必讀）

- `planning/devops/authentik-stack.md` §5.2.1 — `cf-google-init` flow 機制
- `web/vite.config.ts` + T-070 ticket（`tickets/DONE/T-070-*.md`）— dev proxy topology、`changeOrigin: false`、nginx `$host` 去 port 的決策脈絡
- memory `reference_authentik_web_path_nginx_routing` — nginx `/oauth/` 反代規則
- memory `feedback_verify_oauth_flow_via_cdp_before_ship` — 為什麼這類問題只有 CDP 真瀏覽器測得到

---

## 候選修法（plan 時收斂）

1. **SPA 直接導到 `:80` origin 的 flow interface** —— `buildSourceInitUrl` 在 dev 產**絕對** `http://localhost/oauth/if/flow/cf-google-init/?next=...`。flow interface 從 `:80` 載 → 它的 XHR 打 `http://localhost/oauth/api/...` 同源、無 CORS。最後 `redirect_uri` 仍是 `http://localhost:5173/auth/callback` 把人帶回 SPA。需要 SPA 知道 Authentik 的 `:80` origin（新 env var 或讓 `authorizeUrl` 在 dev 設絕對）。
2. **nginx / Authentik 加 CORS headers** —— dev-only 對 `/oauth/api/` 回 `Access-Control-Allow-Origin: http://localhost:5173` + `Allow-Credentials: true`。較侷限但改動面小。
3. **讓 Authentik emit 相對 `base_url`** —— 若 Authentik 有設定能讓 `base_url` 相對化（`/oauth/` 而非 `http://localhost/oauth/`），XHR 就會是相對路徑、走 vite proxy 同源。需查 Authentik 2024.12.5 有沒有這個開關。

plan 時三條都評估；偏好不破壞 T-070、不增 prod 表面積的那條。

---

## Acceptance criteria

- [ ] CDP re-verify：fresh session → `:5173/login` → 「使用 Google 登入」→ flow interface **不卡 `Loading…`**、RedirectStage 有跑 → Google → callback → **落在 SPA Dashboard**
- [ ] CDP console 無 CORS error
- [ ] T-070 的 Google `redirect_uri` 行為不變、proxy hop 仍通
- [ ] CI e2e（同源路徑）維持綠
- [ ] 對應 planning doc（§5.2.1 或 T-070 的 dev-proxy 說明）更新

---

## Files expected to touch

- `web/src/lib/oauth-client.ts` 或 `web/vite.config.ts` 或 `infra/nginx/*`（視收斂的修法）
- `web/.env.example` / `.env`（若加 env var）
- `planning/devops/authentik-stack.md` §5.2.1（edit）
- `STATUS.md` (edit)

> **E2E coverage gate（CONTRIBUTING §3.5）：** 視修法而定 —— 若改 SPA URL builder 行為，更新 `oauth-login.spec.ts`；若純 nginx / env 改動則可能 N/A。實作者於 PR 說明。dev-`:5173` 真瀏覽器路徑結構上 CI 覆蓋不到（同 T-070），靠 CDP 手動驗。

---

## OAuth scope required（後端 endpoint 必填；frontend / docs / infra 票寫 `n/a`）

`n/a`

---

## MCP tool delta（agent surface 影響；無影響寫 `n/a`）

`n/a`

---

## Notes

### 為什麼 T-073 / T-075 的 CI + e2e 沒抓到

CI e2e 整套跑在 `nginx:80` 同 origin —— flow interface 跟 Authentik API 同源、沒有 CORS preflight。`:5173` dev origin 是 vite proxy 拼出來的「假同源」，flow interface 的絕對-URL XHR 戳破了這個假象。典型「CI 看不到的 dev-only topology 缺口」，跟 T-070 同類。只有 CDP 連真瀏覽器走 `:5173` 才測得到。

### 牆的順序（operator 首登 dev 路徑）

T-068 reveal → wall 1（source flow 沒設，T-069）、wall 2（backend User row，T-071）、wall 3（`next` 不 redirect，T-073）；T-070 dev proxy；T-073 的 `?query=` encoding bug（T-075）；**wall 4 = 本單（flow interface XHR CORS）**。
