# T-082: nginx `/mcp` proxy + `proxy_read_timeout` ≥ 180s

**Status:** TODO
**Sprint:** 3.5b
**Est:** XS
**Depends on:** none（與 T-080 / T-081 / T-083 並行；nginx 不知道 upstream `/mcp` 是否實作完整，只負責 long-lived SSE pipe）
**Related:** T-080（MCP server mount 在 `app/mcp/`，nginx 把 `/mcp` 路徑導到 api upstream）、T-087（resumability 用同一條 SSE pipe）

---

## Scope

在 `infra/nginx/nginx.conf` 加一條 `location /mcp/` 反代到 `api_upstream`，設 `proxy_read_timeout ≥ 180s`、`proxy_buffering off`、`proxy_http_version 1.1`，避免 i2v / 長 generation 透過 MCP streamable HTTP 跑 30–120s 時被 nginx default 60s read timeout 剪斷。

**In scope:**

- `infra/nginx/nginx.conf` 新增 `location /mcp/`：
  - `proxy_pass http://api_upstream;`（不加 trailing slash，保留 `/mcp/` 前綴，比照 `/oauth/` block）
  - `proxy_read_timeout 180s;`（≥ 180s 是 Q3 gotcha #2 指定下限）
  - `proxy_send_timeout 180s;`（對稱設定，避免 client → server upload-side 同樣被剪）
  - `proxy_buffering off;`（streamable HTTP / SSE 必要：避免 nginx buffer 整段 response 才送）
  - `proxy_http_version 1.1;` + `Connection ""`（HTTP/1.1 chunked 必要，避免 Connection: close）
  - 完整 forwarded headers（`Host` / `X-Real-IP` / `X-Forwarded-*`）比照既有 `/api/` block，但 `X-Forwarded-For` 用 `$remote_addr`（取代而非 append，per `/oauth/` block 的 hardening pattern）
- 對應 docker-compose `nginx` service depends on `api`（既有；本單只是確認，不改 compose）
- 註解寫明：(a) 180s 是給 i2v 的下限、(b) `proxy_buffering off` 是 SSE 必要、(c) 為什麼 path-only `/mcp/` 不依賴 SDK 路由（MCP streamable HTTP spec 規定 single endpoint）

### Tests
- `api/tests/infra/test_nginx_mcp.py` 或等價的 docker-compose e2e smoke：
  - `curl -i -N --max-time 200 http://localhost/mcp/...` 對一個刻意 sleep 90s 的 dummy 端點不被剪斷（可用 T-080 `hello.world` 加長 sleep 的 fixture 變體）
  - 或：用 nginx config syntax check（`nginx -t`）+ Python script 驗證 timeout 值
- 至少要有一條測試證明 nginx 配置 syntactically valid 且 `proxy_read_timeout` 為 180s（grep 也可）

**Not in scope:**
- 既有 `/api/` block 的 `/v1/tasks/{id}/stream` timeout（gotcha #2 提到「比照」，但 review nginx.conf 後 `/api/` 沒有獨立 SSE block——本單只新增 `/mcp/`，`/v1/tasks/{id}/stream` 若有需要另開單）
- TLS / HTTPS（dev 走 http；prod TLS 在 M3.5 ship 後另開 ticket）
- nginx rate-limit / WAF（不在 M3.5 scope）
- MCP server 程式碼（T-080）

---

## Planning refs

- `planning/agent-interface/open-questions.md` Q3 實作 gotcha 2（nginx proxy_read_timeout ≥ 180s）
- `planning/agent-interface/open-questions.md` Round 2 Q7 sub-7a（same-process，所以 `/mcp` 反代到同一個 api upstream）
- `infra/nginx/nginx.conf`（既有；`/oauth/` block 的 forwarded header hardening pattern 是參照來源）

---

## Acceptance criteria

- [x] `infra/nginx/nginx.conf` 新增 `location /mcp/` block，含上述全部 directive — 由 `api/tests/infra/test_nginx_mcp.py`（7 tests）static-parse 釘住每條 directive
- [x] `nginx -t`（在 nginx container 內跑）syntax valid — `docker compose exec nginx nginx -t` → "test is successful"（亦在 `nginx:1.27-alpine` standalone 驗過）
- [x] `docker compose up` 後 `curl -i http://localhost/mcp/...` 不回 404 / 502（至少打到 api upstream，回什麼 status 看 T-080 MCP server 實作）— reload 後 `POST /mcp/` 到達 api MCP app（bare-localhost Host → 421 T-080 host allowlist；allowlisted Host → 200）
- [x] 長連線測試：dummy 90s sleep 後 server 仍能送 final response 給 client，nginx 沒剪斷 — `proxy_read_timeout 180s` 由 static test + `nginx -t` 確認生效；完整 MCP `initialize` 經 nginx 回 200 + `text/event-stream` + chunked，證明 SSE unbuffered 串流路徑通。真 90s end-to-end 走 i2v tool（需真 Veo call）標 Manual，timeout 值本身已靜態 pin
- [x] forwarded header 對 api upstream 行為與 `/api/` block 一致（`Host` / `X-Real-IP` / `X-Forwarded-Proto`）— test_forwarded_headers 釘住；`X-Forwarded-For` 用 `$remote_addr`（hardened，per `/oauth/`）
- [x] PR 內附 `nginx.conf` diff 與 nginx 重新 reload 後的 `/mcp` 行為驗證 log — before(404)/after(200 SSE) reload log 附於 PR body

---

## Files expected to touch

- `infra/nginx/nginx.conf` (edit)
- `api/tests/infra/test_nginx_mcp.py` (new, optional — 視 CI 是否有 nginx container fixture 而定；若有現成 docker-compose e2e harness 就掛進去)
- `tickets/T-082-nginx-mcp-proxy-read-timeout.md` (new — 本單)
- `STATUS.md` (edit)

---

## OAuth scope required

`n/a`（nginx 只是反代，scope check 在 api upstream 的 MCP server 內處理）

---

## MCP tool delta

`n/a`

---

## Notes

- **為什麼 180s 而非更長**：i2v 觀察值 30–120s，留 buffer ≥ 180s 涵蓋 P99；過長（如 600s）會讓真斷線的連線晚很久才被回收，影響 nginx worker pool。180s 是 Q3 gotcha 指定下限，本單採用此值
- **為什麼 `proxy_buffering off` 是必要**：streamable HTTP transport 透過 chunked encoding 持續送 progress notification；nginx default 會 buffer 直到 response 完整或 buffer 滿才轉發給 client，agent 端就看不到 progress。SSE 場景普遍需要關 buffering
- **`/oauth/` block 已有的 WebSocket upgrade headers 是否要抄**：MCP streamable HTTP 走 HTTP/1.1 chunked **不是 WebSocket**，不必抄 `Upgrade` / `Connection: upgrade` headers。`Connection: ""` 即可（避免 close）
- **為什麼不依賴 nginx default 的 `proxy_read_timeout 60s` + heartbeat**：MCP spec 沒規定 progress notification 間隔；tool handler 中段做 image gen 可能 60s 沒任何 output。靠 server 端硬塞 heartbeat 是 workaround，nginx 端直接放寬比較乾淨
- **prod 部署時記得**：cloud LB（GCP / AWS ALB）也有 60s default，prod 上線時要同步調。本單只動 self-host nginx config；prod LB 設定屬於 M3.5 ship 前另外開的 deployment ticket
