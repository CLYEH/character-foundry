# T-072: nginx `/api/health` returns 502 from inside the docker network

**Status:** TODO
**Sprint:** Backlog（post-3.5a；T-070 investigation reveal）
**Est:** XS
**Depends on:** none
**Related:** T-046（nginx `/storage/` proxy）、T-070（dev-proxy topology 驗證時撞到這條）

---

## Scope

查清楚為什麼從 docker network 內部打 `http://nginx/api/health` 會回 **502**，並修掉（或確認無害並文件化）。

T-070 驗 vite dev-proxy topology 時，從 `web` container 內實測：

| target | 結果 |
|---|---|
| `http://api:8000/health` | 200 ✅ |
| `http://nginx/oauth/application/o/authorize/` | 404（= 有打到 Authentik）✅ |
| `http://nginx/api/health` | **502** ❌ |

e2e 走 nginx:80 且綠，代表 nginx 的 `/api/` 對**真實 API 路由**是通的 —— 所以這多半是 `/health` 這條 path 特有的小問題，不是 `/api/` proxy 整段壞掉。但 502 本身沒被解釋過，留著是個未知數。

**In scope:**

1. Root-cause：為什麼 `http://nginx/api/health` 502。可能方向（plan 時收斂）：
   - `/health` 是 top-level route（不在 `/v1` 下），nginx `/api/` block 的 prefix strip 後變成 `api_upstream/health` —— 對不對？
   - upstream timeout / connection 行為
   - `/health` 對 nginx `location /api/` 的 `proxy_pass http://api_upstream/;`（含 trailing slash strip）的互動
2. 修掉，或確認「e2e 真實路由已涵蓋、`/health` 這條 502 無 user-facing 影響」並在 `nginx.conf` 留 comment 說明。

**Not in scope**（保留給其他單）：

- vite dev-proxy 設定（T-070 已處理；T-070 的 `/api` proxy 直接打 `api:8000`，不經 nginx，所以這條 502 不影響 T-070）
- nginx `/oauth/` 路由（memory `reference_authentik_web_path_nginx_routing` 已涵蓋）

---

## Planning refs（開工前必讀）

- `infra/nginx/nginx.conf` —— `location /api/` block；`proxy_pass http://api_upstream/;` 的 trailing slash 行為
- `planning/backend/api-shape.md` —— `/health` route 掛在哪一層（top-level vs `/v1`）
- `tickets/DONE/T-070-vite-dev-oauth-proxy.md` Notes —— 502 是在那張單的 topology 驗證裡發現的

---

## Acceptance criteria

- [ ] `http://nginx/api/health` 從 docker network 內回 200（或：確認無害 + `nginx.conf` 留 comment 解釋為什麼 502 是 expected 且無影響）
- [ ] e2e 仍綠（不可為了修這條把真實 `/api/` 路由弄壞）
- [ ] root cause 寫進 PR 或 ticket Notes

---

## Files expected to touch

- `infra/nginx/nginx.conf` (edit) —— 視 root cause 而定
- `STATUS.md` (edit)

> **E2E coverage gate（CONTRIBUTING §3.5）：** 預期 **N/A — infra**。改的是 nginx 設定；既有 e2e（走 nginx:80）已覆蓋真實 `/api/` 路由且必須維持綠。

---

## OAuth scope required（後端 endpoint 必填；frontend / docs / infra 票寫 `n/a`）

`n/a`

---

## MCP tool delta（agent surface 影響；無影響寫 `n/a`）

`n/a`

---

## Notes

優先度低 —— e2e 綠證明 user-facing 路由沒事，這只是一條內部 health-check path 的未解釋 502。但「未解釋的 502」放著總是地雷，XS 成本查清楚值得。

T-070 ticket 的 "Not in scope" 段已預告要開這張單。
