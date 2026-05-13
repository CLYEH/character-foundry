# T-053: Authentik 設定 Google upstream IdP + client 註冊

**Status:** DONE
**Sprint:** 3.5a
**Est:** M
**Depends on:** T-052（Authentik service 必須先起來）, T-061（Secret scan + SAST 必須先 land，否則 `client_secret` 進 repo 歷史就晚了；見 `planning/harness/roadmap.md` §1 A4）
**Related:** T-054（後端要驗 token，需要本單登好的 client）

---

## Scope

把 Authentik 設成 OAuth provider：
1. 接 Google Workspace 當 upstream IdP（Sign in with Google）
2. 註冊 5 個 OAuth client（4 個外部 agent + 1 個 frontend SPA）
3. 在 Authentik 內定義 5 條 scope
4. 在 backend repo 內建立 `app/auth/mcp_clients.py` allowlist（pre-registered，Figma 模式）

**In scope:**

### Authentik 設定（admin UI 操作 + 文件化）
- Source: Google OAuth（client_id / client_secret 從公司 Workspace admin console 拿）
- Application：`character-foundry-spa`（給前端 SPA，Auth Code + PKCE）
- Application × 4：`claude-code` / `vs-code` / `cursor` / `cf-test-agent`（client_id + secret 或 PKCE-only 視 client 屬性）
- Scopes：`character:read` / `character:write` / `task:read` / `task:cancel` / `usage:read`
- Per-client scope policy 用 Authentik Group + Policy 綁定

### Backend allowlist
- `app/auth/mcp_clients.py` 新建 module-level dict，per Round 2 Q7c：
  ```python
  M2M_DEFAULT_SCOPES = ["character:write", "task:read"]

  ALLOWED_CLIENTS = {
      "claude-code": {"scopes": None},   # None = 走 default（人授權的 delegated client）
      "vs-code": {"scopes": None},
      "cursor": {"scopes": None},
      "cf-test-agent": {
          "scopes": ["character:read", "character:write", "task:read", "task:cancel", "usage:read"],
      },
  }
  ```
  （`scopes=None` 表示 delegated client，consent 時 user 同意給什麼就拿什麼；`scopes=list` 表示 M2M 顯式覆寫）

### 設定步驟文件化
- `planning/devops/authentik-stack.md` 加 §5「Initial setup」記錄 admin UI 點擊步驟（截圖選擇性）

**Not in scope:**
- Backend 對接 token（T-054）
- Frontend Sign in with Google 按鈕（T-056）
- Authentik 換 provider / 升級流程（M3.5 ship 後）

---

## Planning refs

- `planning/auth/open-questions.md` Q1（Authentik + Google upstream）
- `planning/auth/open-questions.md` Q3 + agent-interface Q5 sub-5a（5 scope + narrow default + per-client 覆寫）
- `planning/agent-interface/open-questions.md` Q7 sub-7c（pre-registered allowlist 在 `app/auth/mcp_clients.py`）
- `planning/devops/authentik-stack.md` §3.1 / §3.2 — networking + secrets

---

## Acceptance criteria

- [ ] Google login 在 Authentik admin UI 跑通：用公司 Workspace 帳號可進 Authentik admin
- [ ] 5 個 scope 在 Authentik UI 內定義齊全（name 與 backend `app/auth/scopes.py` 對齊，**注意** T-054 才建這檔；本單先在文件確認 name list）
- [ ] 5 個 application（1 SPA + 4 agent）註冊好，每個有自己的 client_id；secret / PKCE 設定符合該 client 屬性
- [ ] `cf-test-agent` 在 Authentik Group + Policy 內顯式拿到 5 scope；其他 3 個 agent 走 narrow default
- [ ] `app/auth/mcp_clients.py` 建立，內容對齊 Authentik 設定
- [ ] `planning/devops/authentik-stack.md` §5 setup 步驟完整可重現
- [ ] 用 `curl` 跑 `client_credentials` grant 對 `cf-test-agent` 成功拿到 access token，token claims 含正確 scope set

---

## Files expected to touch

- `api/app/auth/mcp_clients.py` (new) — allowlist module
- `planning/devops/authentik-stack.md` (edit) — 加 §5 setup steps
- `.env.example` (edit) — `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET`
- `planning/devops/environment-variables.md` (edit)
- `tickets/T-053-authentik-idp-and-clients.md` (new — 本單)
- `STATUS.md` (edit)

---

## OAuth scope required

`n/a`（不開新 endpoint；本單是 provider 設定）

---

## MCP tool delta

`n/a`

---

## Notes

- **Google Workspace 端**：需要 Workspace admin 在 Google Cloud Console 開一個 OAuth client 給 Authentik 用，redirect URI 指向 nginx-proxied 路徑 `https://<authentik-host>/oauth/source/oauth/callback/google/`（含 `/oauth/` 前綴；nginx 反代後內部對應 Authentik slug-derived `/source/oauth/callback/google/`）。這步可能要請公司 IT；本單列為先決條件
- **scope naming 對齊**：Authentik 內定義的 scope 字串必須跟 backend `require_scope("character:write")` 用的字串完全一致。建議在 `planning/auth/open-questions.md` 決策紀錄 Q3 補上 canonical 字串清單，本單照抄
- **Token introspection vs JWT verify**：T-054 會走 JWT verify（Authentik 用 RS256 簽），不走 introspection（省一次 API call）。本單不直接涉及，但 Authentik 設定要確保 access token 是 JWT 格式（非 opaque）
- **`scopes=None` 的語意**：delegated client 不在 backend allowlist 限制 scope；scope 由 user 在 consent 時決定。Backend `require_scope` 純驗 token 上有沒有對的 scope，allowlist 只負責「client_id 是否認識」
