# MCP OAuth Discovery — auto-login for human-driven MCP clients (T-089)

> **Status:** Locked (2026-06-08, plan-phase 拍板 — agent-interface + auth view)
> **Trigger:** 使用者 2026-05-21 在 T-084 手測時提出「期待 MCP client 自己導去登入頁取 token」。
> **Upstream dep:** ✅ S3.5-6 / T-093（delegated token 現在帶 5 條 app scope）。

This is the **human-delegated** half of M3.5 agent-native. The headless / M2M half
(T-080 dual-stack bearer + T-091 client_credentials smoke) already lets a server-side
agent mint a token directly. This doc covers letting a **human-driven** MCP client
(Claude Desktop / claude.ai connector / MCP Inspector / Cursor) connect to `/mcp/`,
**auto-discover** how to authenticate, send the human through the existing
Authentik/Google login, get a delegated token, and call `character.*` — with **no
manual Bearer-token paste**.

---

## 1. The discovery chain (RFC 9728 + OAuth 2.1 Auth Code + PKCE)

```
client → POST /mcp/ (no token)
       ← 401 + WWW-Authenticate: Bearer resource_metadata="<host>/.well-known/oauth-protected-resource"
client → GET <host>/.well-known/oauth-protected-resource          (RFC 9728 PRM)
       ← { resource: "<host>/mcp",
           authorization_servers: ["<host>/oauth/application/o/character-foundry-mcp/"],
           scopes_supported: [5 canonical], bearer_methods_supported: ["header"] }
client → GET <issuer>/.well-known/openid-configuration             (Authentik AS metadata)
       ← { authorization_endpoint, token_endpoint, jwks_uri, ... }
client → opens browser → Authentik (Google SSO / password) → explicit consent
       ← authorization code (PKCE) → token_endpoint → delegated access token (1h, no refresh)
client → POST /mcp/ (Bearer <token>) → tools/list + character.list ✓
```

Implemented in `api/app/mcp/discovery.py` (PRM endpoint) +
`api/app/mcp/auth.py::MCPAuthContextMiddleware` (the 401 trigger). The PRM's
`resource` / `authorization_servers` / metadata URLs are built from the public base
URL — derived from the request (`X-Forwarded-Proto` + `Host`, forwarded by nginx) or
pinned via `MCP_PUBLIC_BASE_URL`.

---

## 2. Locked decisions

### 2.1 One dedicated Authentik app `character-foundry-mcp`
All human MCP clients authenticate through **one** Authentik application (public +
PKCE), so the PRM advertises a **single** authorization server and the token's `iss`
matches the discovered issuer (Authentik runs per-provider issuer mode — multiple
apps would mean multiple issuers and a messy discovery match). Each client brand's
redirect_uri is registered on this one app. `ALLOWED_CLIENTS` gains
`character-foundry-mcp`; the pre-existing `claude-code`/`vs-code`/`cursor` entries
stay (accepted if manually configured) but are **not** advertised in PRM.

### 2.2 401 trigger line: only on a fully-missing `Authorization` header
- **No `Authorization` header at all** → `401 + WWW-Authenticate` (the discovery
  trigger). The whole MCP transport (`initialize` / `tools/list` / every tool) now
  requires an authenticated client — standard MCP-spec posture (the client completes
  the 401→auth handshake before listing/calling tools).
- **Token present but invalid / expired / wrong-client / insufficient-scope** → keeps
  T-080's `200 + structured tool-error` (AgentError envelope). A manually-configured
  client that sent a bad token gets a readable reason, not a re-discovery loop.

  ⚠ **Consequence:** `meta.get` is no longer anonymously reachable over MCP (T-088
  had made it so). It is still gated by **no scope** (any authenticated caller), and
  the same capability data stays anonymously public over REST `/v1/meta`. This falls
  out of enabling discovery at all — either ratified 401 line requires a token before
  any tool call.

### 2.3 Audience binding — Phase-1 pragmatic (deferred strict RFC 8707)
Authentik 2024.12 issues `aud = client_id`, not `aud = <MCP resource URI>`. Phase 1
validates `aud ∈ registered client_ids` + allowlist + `iss` (the existing
`verify_oauth_token` checks). Strict RFC 8707 resource-indicator audience-binding of
the token to the `/mcp` resource is a documented Phase-2 follow-up, not a blocker.

---

## 3. Client conventions (what each MCP client fills in)

Every human MCP client uses **`client_id = character-foundry-mcp`** (public, PKCE, no
client secret). The redirect_uri is fixed by each client; the Authentik app registers
the union (see `infra/authentik/blueprints/cf-e2e-bootstrap.yaml` + the dev/prod
runbook `planning/devops/authentik-stack.md` §5.4):

| Client | client_id | redirect_uri | Notes |
|---|---|---|---|
| MCP Inspector (OAuth mode) | `character-foundry-mcp` | `http://localhost:6274/oauth/callback` | covered by the loopback regex; the AC #3 starting point |
| Claude Desktop / native CLI | `character-foundry-mcp` | `http://127.0.0.1:<ephemeral>/...` | RFC 8252 loopback (regex), PKCE-protected |
| claude.ai remote connector | `character-foundry-mcp` | `https://claude.ai/api/mcp/auth_callback` | strict https entry — **confirm exact value during the Manual E2E** |

Server URL the client points at: `https://<host>/mcp` (it discovers the rest). Most
DCR-only clients accept a manual `client_id`; DCR stays off (agent-interface
open-questions 7c).

---

## 4. Operator persona pass (planning/CLAUDE.md §1)

1. **Admin / config path** — the MCP OAuth app is created via the
   `cf-e2e-bootstrap.yaml` blueprint (dev/e2e) or the Authentik admin UI
   (`http://localhost:9000/if/admin/` → Applications, prod), per authentik-stack.md
   §5.4. Issuer/aud go in `.env` (`AUTHENTIK_ISSUER_URL` / `AUTHENTIK_AUDIENCE`); the
   public base URL is derived from the request or pinned via `MCP_PUBLIC_BASE_URL`.
2. **Break-glass / recovery** — if discovery ever breaks, MCP stays usable: the
   dual-stack **manual Bearer token** path (T-080) and the **M2M client_credentials**
   path (T-091) are untouched. A human can mint a token (curl / reuse a JWT) and paste
   it into the client config. Discovery failure degrades to "manual token", never
   "MCP unusable".
3. **Topology vs main user flow** — PRM + the 401 are invisible to humans (the MCP
   client handles them). Login reuses the same Authentik/Google SSO page as the SPA;
   the operator/admin entry is the same Authentik admin UI as all other auth config.

---

## 5. Verification

- CI: PRM document shape + base-URL derivation/override, and the no-token `/mcp/` →
  401 + `WWW-Authenticate` trigger (`api/tests/mcp/test_discovery.py`); the
  present-but-bad-token → 200 tool-error regressions stay green
  (`api/tests/mcp/test_skeleton.py`).
- Manual E2E (AC #3): MCP Inspector (OAuth mode) → `http://localhost/mcp` → auto
  discover → Authentik/Google login → consent → delegated token → `tools/list` +
  `character.list`. Follow the CDP/manual OAuth pattern (memory
  `feedback_verify_oauth_flow_via_cdp_before_ship`).

## 6. 關聯

- `../auth/open-questions.md` — T-089 decision record + Q8 (MCP-OAuth integration)
- `../devops/authentik-stack.md` §5.4 (app + redirect_uri conventions), §5.9 checklist
- `api/app/mcp/discovery.py`, `api/app/mcp/auth.py` — implementation
- `endpoint-mcp-mapping.md` — tool surface the delegated token reaches
