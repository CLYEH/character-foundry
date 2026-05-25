# T-090: MCP host allowlist accepts bare hosts + document `MCP_ALLOWED_HOSTS` for deployment

**Status:** TODO
**Sprint:** Backlog（post-3.5b；T-082 Codex review #116 P1 拆出）
**Est:** XS
**Depends on:** T-080（`_DEFAULT_ALLOWED_HOSTS` 在 `app/mcp/app.py`；本單改的是它）
**Related:** T-082（nginx `/mcp` 反代，本單從它的 Codex review 拆出）、T-089（真人 MCP client discovery，會踩同一道 host 牆）

---

## Scope

修掉 MCP streamable-HTTP server 在「default port + bare host」情境下回 `421 Invalid Host header` 的 papercut，並把「prod 部署要把 public host 加進 `MCP_ALLOWED_HOSTS`」寫進文件。

### 背景（T-082 ship 時 surface）

T-082 加了 nginx `location /mcp/` 反代（forward `Host $host`，比照 `/api/` `/oauth/`）。Codex review #116 第二條 P1 指出：bare `Host: localhost`（standard client 對 default port **不送 port**，經驗證 `curl http://localhost:80/` 送的就是 `Host: localhost`）打 `/mcp/` 會被 FastMCP `TransportSecuritySettings` 擋下回 421，因為 `app/mcp/app.py::_DEFAULT_ALLOWED_HOSTS = "127.0.0.1:*,localhost:*,[::1]:*"` 的 pattern 是 **port-required**（`localhost:*` 不 match 無 port 的 `localhost`）。

Codex 原建議「nginx 改 forward `$http_host`」**經驗證無效**：default-port client 的 Host header 本來就不帶 port，`$http_host` == `$host`（都是 bare），改了照樣 421。真正的修點在 app 端的 allowlist default，不是 proxy header。這屬 T-080 transport-security 範疇，T-082「Not in scope: MCP server 程式碼（T-080）」明確排除，故拆本單。

⚠ 注意這**不影響使用者目前的 ngrok 部署**：dev `.env` 的 `MCP_ALLOWED_HOSTS` 已手動加了 bare ngrok host（`inheritable-fleshly-jameson.ngrok-free.dev` + `:*`），所以實際在用的路徑回 200。本單修的是 (a) 純 localhost dev 測試的 out-of-the-box 體驗、(b) 把 prod allowlist 設定變成有文件依據而非口耳相傳。

### In scope

- `app/mcp/app.py::_DEFAULT_ALLOWED_HOSTS` 補上 bare-host 形式（`127.0.0.1`、`localhost`、`[::1]`）與既有 `:*` 形式並存，讓 default-port 的 loopback 存取 out-of-the-box 不 421。
  - ⚠ 這是 DNS-rebinding 保護的 default，改前確認 FastMCP `TransportSecuritySettings` 對「bare host entry」的 match 語意（是否 exact-match host、是否影響保護強度）；保護目的是擋 rebinding，加 loopback bare host 不削弱對外部 host 的防護。
- `planning/devops/environment-variables.md`（或對應 §）+ `.env.example`：明寫 prod 部署**必須**把 public host 加進 `MCP_ALLOWED_HOSTS`（同 `MCP_ALLOWED_ORIGINS`），附 ngrok / 自訂 domain 範例。
- 對齊 T-080 STATUS note「production behind nginx will need to allowlist the public host」—— 把它從 note 升成有 ticket + 文件。

### Not in scope（保留給其他單）

- nginx `/mcp` 反代設定（T-082 已 ship；本單不動 `nginx.conf`，已確認 `$host` 不是問題點）
- cloud LB（GCP/AWS ALB）60s read timeout（T-082 Notes 提到，屬 M3.5 ship-prep deployment ticket）
- 真人 MCP client OAuth discovery（T-089）

---

## Planning refs（開工前必讀）

- `api/app/mcp/app.py` `_build_transport_security` / `_DEFAULT_ALLOWED_HOSTS` — 改的就是這
- `planning/agent-interface/open-questions.md` Round 2 Q7 sub-7b — host 驗證是 defense-in-depth、真正 guarantee 在 OAuth allowlist
- T-082 PR #116 Codex thread（comment 3295887598）+ 「Notes for reviewer」段 — 觸發脈絡與經驗證據

---

## Acceptance criteria

- [ ] `curl -X POST http://localhost/mcp/`（bare `Host: localhost`，default port）不再回 421，往下走到 JSON-RPC handling
- [ ] 既有 `:port` 形式（含 dev ngrok host）仍 work，無 regression
- [ ] `.env.example` + devops env doc 寫明 prod 要設 `MCP_ALLOWED_HOSTS` 的 public host，附範例
- [ ] DNS-rebinding 保護對「非 allowlist 的外部 host」仍擋（加 bare loopback 不開後門）
- [ ] 測試綠：`app/mcp/app.py` 的 allowlist 建構單元測試覆蓋 bare-host + port 兩形式 match；現有 `tests/mcp/test_skeleton.py` 不 regress

---

## Files expected to touch

- `api/app/mcp/app.py`（edit `_DEFAULT_ALLOWED_HOSTS`）
- `api/tests/mcp/test_skeleton.py` 或新 test（allowlist match 兩形式）
- `.env.example`（edit）
- `planning/devops/environment-variables.md`（edit）
- `STATUS.md`（edit）

---

## OAuth scope required

`n/a`

---

## MCP tool delta

`n/a`
