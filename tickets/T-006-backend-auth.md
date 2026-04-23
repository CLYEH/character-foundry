# T-006: Backend Auth (JWT login / refresh / logout / me)

**Status:** TODO
**Sprint:** 1
**Est:** M (2h)
**Depends on:** T-001, T-002
**Related:** T-008（Frontend 串 auth）

---

## Scope

完整 JWT auth 流程 + middleware + admin CLI 建使用者（Phase 1 無公開註冊）。

**In scope:**
- Password hashing（argon2）
- JWT utils（簽 access / refresh、驗簽、decode payload）
- Routes：
  - `POST /v1/auth/login` — email + password → tokens
  - `POST /v1/auth/refresh` — refresh_token → 新 access
  - `POST /v1/auth/logout` — revoke refresh token
  - `GET /v1/auth/me` — 目前使用者資料
- Refresh token 存 DB 表 `refresh_tokens`（新增 migration）
- FastAPI dependency：`get_current_user()` 過濾 JWT
- Middleware 全域處理 `AUTH_*` 錯誤（401 回 AgentError）
- Admin CLI：`python -m app.cli create-user --email ... --password ... --name ...`
- `AgentError` 類別完整化（若 T-005 只做 base，這裡補齊所有 `AUTH_*` code）

**Not in scope:**
- 公開註冊頁 / forgot password（Phase 1 不做）
- OAuth（Phase 2）
- Rate limiting 單獨實作（T-xxx backend iter 3 做）

---

## Planning refs

- `planning/backend/api-shape.md` §2 Auth endpoints
- `planning/backend/api-shape.md` §4 AgentError schema + §4.1 錯誤代碼
- `planning/data/db-schema.md` §3.2 users
- `planning/devops/environment-variables.md` §2.1 JWT env vars
- `DECISIONS.md` §6 B4 — JWT 帳密

---

## Acceptance criteria

- [ ] Admin CLI 能建 user：`python -m api.app.cli create-user --email a@b.com --password ...`
- [ ] `POST /v1/auth/login` 正確 credential 回 `{access_token, refresh_token, expires_in, user}`
- [ ] 錯密碼回 401 + `AUTH_INVALID_CREDENTIALS`
- [ ] `GET /v1/auth/me` 無 token 回 401 + `AUTH_MISSING_TOKEN`
- [ ] `GET /v1/auth/me` 過期 token 回 401 + `AUTH_EXPIRED`
- [ ] `GET /v1/auth/me` 偽造 token 回 401 + `AUTH_INVALID_TOKEN`
- [ ] `POST /v1/auth/refresh` 用合法 refresh_token 回新 access，expired refresh 回 `AUTH_REFRESH_EXPIRED`
- [ ] `POST /v1/auth/logout` 後該 refresh_token 再用回 `AUTH_REFRESH_REVOKED`
- [ ] Pytest `api/tests/auth/` 綠
- [ ] 設定 access token 有效期 env var 可調（`JWT_ACCESS_TTL_SECONDS`）

---

## Files expected to touch

- `api/alembic/versions/20260423_014_refresh_tokens.py` (new)
- `api/app/models/refresh_token.py` (new)
- `api/app/auth/__init__.py` (new)
- `api/app/auth/passwords.py` (new) — argon2 hash / verify
- `api/app/auth/jwt.py` (new) — sign / verify / decode
- `api/app/auth/service.py` (new) — login / refresh / logout 業務邏輯
- `api/app/api/routes/auth.py` (new) — 4 個 endpoints
- `api/app/api/deps.py` (edit) — 加 `get_current_user()` dependency
- `api/app/middleware/error_handling.py` (new) — 把 `AgentError` 轉 HTTP response
- `api/app/core/errors.py` (edit) — 補齊 `AUTH_*` error code
- `api/app/cli.py` (new) — `create-user` command
- `api/tests/auth/test_login.py` (new)
- `api/tests/auth/test_refresh.py` (new)
- `api/tests/auth/test_me.py` (new)
- `api/tests/auth/test_middleware.py` (new)

---

## Notes

- Argon2 參數用 argon2-cffi 預設，不用自己調
- Refresh token 用 UUID + hash 存 DB（回給使用者的是 UUID，DB 存 hash，logout 時 lookup 標記 revoked）
- Access token payload：`{sub: user_id, team_id, exp, iat, jti}`
- `/auth/logout` 是 refresh token revoke，不是 access token（access 本身 stateless，靠到期）
- CLI 用 `typer` 或 `click`（擇一）
- AgentError 格式嚴格遵循 api-shape.md §4：`{code, message, problem, cause, fix, docs_url, retryable, request_id}`
- `request_id` 從 middleware 注入 context var（T-xxx 可補 request ID middleware，本單可先塞 dummy uuid）
