# Endpoint → MCP Tool Mapping

> **Status:** Locked · 2026-05-19 · Sprint 3.5b (T-083)
> **Owner:** Agent Interface Agent
> **Source of truth:** This doc enumerates every REST endpoint in `../backend/api-shape.md` §2 / §5 and assigns it to an MCP tool (or marks it blacklisted). It is the **権威 input** that Wave B tickets (T-084 / T-085 / T-086) copy `bundles` from.
>
> **Update rule:** Adding or removing a REST endpoint **MUST** synchronously update this doc in the same PR. T-081 may later add a lint script that diffs §2 below against `api/app/api/routes/`; until then this rule is process-enforced.
>
> **Authoritative refs:** Categorization principle from `open-questions.md` Round 3 Q9. Packaging rule from `../backend/oauth-mcp-integration.md` §3.3. Scope strings from `../auth/open-questions.md` §「Q3 canonical scope 字串（T-053 lock）」.

---

## §1. Categorization principle

Every endpoint falls into one of four buckets (per `open-questions.md` Round 3 Q9):

| Bucket | Decision | Rationale |
|---|---|---|
| ❌ **Ops** | `GET /health`（DevOps monitoring）| Agents have no reason to call infra liveness. |
| ❌ **Auth** | `/v1/auth/*`（OAuth-replaced）| OAuth flow is human-side (SPA) or M2M (client credentials). Agents don't `username/password`; exposing login endpoints to MCP creates a confused contract. |
| ❌ **Pure-UI redirect** | `GET /v1/exports/{id}/download`（302 → signed URL）| Agent fetches the signed URL directly; an MCP tool that just forwards a 302 carries no agent-readable value. |
| ❌ **Storage serving** | `GET /storage/{key:path}`（binary I/O, signed JWT）| Agent receives `storage_url` fields in tool results and HTTP-fetches them itself. Signed-URL token is decoupled from OAuth per agent-interface Q6 / auth Q5. |
| ✅ **Whitelist** | `/v1/characters/*`, `/v1/aliases/*`, `/v1/motions/*`, `/v1/tasks/*`, `/v1/usage/*`, `/v1/meta`, `/v1/prompt/*` | Domain operations agents need to drive the M3-scope flow end-to-end. |

A ✅ endpoint becomes either a **1:1 wrap** (one MCP tool ↔ one REST endpoint) or part of a **packaged tool** (one MCP tool ↔ ≥2 endpoints). Packaging rule from `oauth-mcp-integration.md` §3.3: "若 agent 為了完成一件事需要連呼 ≥2 個 endpoint, packaging." Single-endpoint cases also package when the endpoint plus its task-polling cycle form one agent mental unit (`motion.generate`, `character.export`).

---

## §2. Endpoint table

**Legend:**
- **M3 status** — `✅` already implemented in `api/app/api/routes/`; `🟡 M4` deferred to Sprint 4 (Download / Copy / Usage / Manifest).
- **MCP tool ticket** — which ticket lands the tool. `M4-future` = M4 ticket carries scope decorator + tool entry from day 1 per `scope.md §1` / `STATUS.md` Sprint 4 note.
- **Scope** — canonical strings from `auth/open-questions.md` §「Q3 canonical scope 字串」. `n/a` for blacklisted.

### §2.1 Characters (api-shape §5.1)

| Method | Path | MCP | Tool / Packaging | Scope | M3 status | Tool ticket | Reason |
|---|---|---|---|---|---|---|---|
| `GET` | `/v1/characters` | ✅ | `character.list`（1:1）| `character:read` | ✅ | T-084 | CRUD list |
| `POST` | `/v1/characters` | ✅ | bundle of `character.create` | `character:write` | ✅ | T-084 | session bootstrap step 1 |
| `GET` | `/v1/characters/{id}` | ✅ | `character.get`（1:1）| `character:read` | ✅ | T-084 | CRUD detail |
| `GET` | `/v1/characters/{id}/manifest` | ✅ | `character.get_manifest`（1:1）| `character:read` | 🟡 M4 | M4-future | agent-friendly metadata snapshot |
| `PATCH` | `/v1/characters/{id}` | ✅ | `character.rename`（1:1）| `character:write` | ✅ | T-084 | CRUD update |
| `DELETE` | `/v1/characters/{id}` | ✅ | `character.delete`（1:1）| `character:write` | ✅ | T-084 | soft delete |
| `POST` | `/v1/characters/{id}/restore` | ✅ | `character.restore`（1:1）| `character:write` | ✅ | T-084 | undo soft delete |
| `POST` | `/v1/characters/{id}/copy` | ✅ | `character.copy`（1:1，async via task）| `character:write` | 🟡 M4 | M4-future | B1 scope = Base + Aliases. Endpoint-level scope is `character:write` only per canonical Q3 mapping; the `character.copy` MCP tool layers `task:read` on top because it internally polls `GET /v1/tasks/{id}` for completion. |
| `GET` | `/v1/characters/{id}/export` | ✅ | bundle of `character.export`（trigger → poll task → resolve signed URL）| `character:read` | 🟡 M4 | M4-future | ZIP packaging is multi-step async. Endpoint-level scope is `character:read` per `auth/open-questions.md §「Q3 ...」` line 150 (all `GET /v1/characters/*` → `character:read`); the packaged `character.export` tool adds `task:read` for internal polling per §3 below. If M4 decides export's mutation-y semantics warrant `character:write`, reopen Q3 in `auth/open-questions.md` first. |
| `GET` | `/v1/exports/{id}/download` | ❌ | n/a | n/a | 🟡 M4 | n/a | 302 redirect; agent fetches signed URL directly |

### §2.2 Creation Session / Checkpoints (api-shape §5.2)

| Method | Path | MCP | Tool / Packaging | Scope | M3 status | Tool ticket | Reason |
|---|---|---|---|---|---|---|---|
| `GET` | `/v1/creation-sessions/{id}` | ✅ | `character.get_session`（1:1, resume / debug）| `character:read` | ✅ | T-084 | inspect in-progress session |
| `POST` | `/v1/creation-sessions/{id}/checkpoints` | ✅ | bundle of `character.create` | `character:write` | ✅ | T-084 | session bootstrap step 2; enqueues async task. Endpoint scope is `character:write` only per canonical Q3 (write side of `/v1/characters/*` family); the `task:read` for polling the returned `task_id` is a packaged-tool-level scope, added in §3 below — REST callers that hold `character:write` but not `task:read` must be able to enqueue. |
| `POST` | `/v1/creation-sessions/{id}/reference-images` | ✅ | bundle of `character.create`（reference mode only）| `character:write` | ✅ | T-084 | session-scoped: backend `assert_session_writable` requires `session.status == "in_progress"` (`api/app/services/checkpoint_service.py:96-98`). After `select-base` the session is `completed` and this endpoint rejects with `CONFLICT_SESSION_NOT_ACTIVE`. **Not** reusable by `alias.add` — see §6 Q-D7 for the backend gap. |
| `POST` | `/v1/creation-sessions/{id}/select-base` | ✅ | bundle of `character.create` | `character:write` | ✅ | T-084 | session bootstrap step 3 (lock Base) |
| `POST` | `/v1/creation-sessions/{id}/abandon` | ✅ | `character.abandon_session`（1:1）| `character:write` | ✅ | T-084 | mark session abandoned |
| `GET` | `/v1/checkpoints/{id}` | ✅ | `character.get_checkpoint`（1:1）| `character:read` | ✅ | T-084 | **Drift from spec — see §6 Q-D1.** Code exists; api-shape §5.2 lists only `POST /{id}/fork`. Used by SPA resume flow to refetch a checkpoint by id. |
| `POST` | `/v1/checkpoints/{id}/fork` | ✅ | `character.fork`（1:1）| `character:write` | ✅ | T-084 | open new character + session from existing checkpoint |

### §2.3 Aliases (api-shape §5.3)

| Method | Path | MCP | Tool / Packaging | Scope | M3 status | Tool ticket | Reason |
|---|---|---|---|---|---|---|---|
| `GET` | `/v1/characters/{id}/aliases` | ✅ | `alias.list`（1:1）| `character:read` | ✅ | T-085 | CRUD list |
| `POST` | `/v1/characters/{id}/aliases` | ✅ | bundle of `alias.add` | `character:write` | ✅ | T-085 | alias creation; enqueues async task. Endpoint scope is `character:write` only per canonical Q3 — `task:read` for the returned `task_id` lives at the packaged-tool level (§3 below). **image / mixed modes:** `reference_image_ids` must be existing ids from the Base's source creation session (`alias_service._resolve_reference_keys` doc string: "Phase 1 has no separate alias reference upload endpoint — refs piggyback on the creation session that made the Base"). Brand-new uploads at alias time blocked by §6 Q-D7. |
| `POST` | `/v1/characters/{id}/aliases/masks` | ✅ | bundle of `alias.add`（inpaint mode: required；mixed mode: optional）| `character:write` | ✅ | T-085 | **Drift from spec — see §6 Q-D2.** Code exists; api-shape §5.3 only mentions a `mask` field in the create body. Mask PNG upload primitive — `alias_service._validate_input_mode_matrix` and T-085 schema both confirm `mask` is required for `inpaint` and optional for `mixed`. |
| `GET` | `/v1/aliases/{id}` | ✅ | `alias.get`（1:1）| `character:read` | ✅ | T-085 | CRUD detail |
| `PATCH` | `/v1/aliases/{id}` | ✅ | `alias.rename`（1:1）| `character:write` | ✅ | T-085 | CRUD update |
| `DELETE` | `/v1/aliases/{id}` | ✅ | `alias.delete`（1:1）| `character:write` | ✅ | T-085 | soft delete |

### §2.4 Motions (api-shape §5.4)

| Method | Path | MCP | Tool / Packaging | Scope | M3 status | Tool ticket | Reason |
|---|---|---|---|---|---|---|---|
| `GET` | `/v1/bases/{id}/motions` | ✅ | `motion.list_for_base`（1:1）| `character:read` | ✅ | T-086 | CRUD list under Base |
| `GET` | `/v1/aliases/{id}/motions` | ✅ | `motion.list_for_alias`（1:1）| `character:read` | ✅ | T-086 | CRUD list under Alias |
| `POST` | `/v1/bases/{id}/motions` | ✅ | bundle of `motion.generate`（`parent_type='base'`）| `character:write` | ✅ | T-086 | i2v generation; polymorphic on parent. Endpoint scope `character:write` only — `task:read` is on the packaged tool, not the REST endpoint. |
| `POST` | `/v1/aliases/{id}/motions` | ✅ | bundle of `motion.generate`（`parent_type='alias'`）| `character:write` | ✅ | T-086 | same tool, alias parent — single agent mental unit. Endpoint scope `character:write` only — see Base motion row above for the tool-vs-endpoint split. |
| `GET` | `/v1/motions/{id}` | ✅ | `motion.get`（1:1）| `character:read` | ✅ | T-086 | CRUD detail |
| `PATCH` | `/v1/motions/{id}` | ✅ | `motion.rename`（1:1）| `character:write` | ✅ | T-086 | rename custom motion |
| `DELETE` | `/v1/motions/{id}` | ✅ | `motion.delete`（1:1）| `character:write` | ✅ | T-086 | soft delete |

### §2.5 Tasks (api-shape §5.5)

| Method | Path | MCP | Tool / Packaging | Scope | M3 status | Tool ticket | Reason |
|---|---|---|---|---|---|---|---|
| `GET` | `/v1/tasks/{id}` | ✅ | `task.get`（1:1）| `task:read` | ✅ | T-080 (registry slot) / Wave B | poll task state; complements MCP `progress` notification path |
| `GET` | `/v1/tasks/{id}/stream` | ✅ | absorbed into packaged tools via MCP `notifications/progress`（per Round 1 Q3 Option A）| `task:read` | ✅ | T-080 | **No direct 1:1 tool.** SSE → MCP progress is the agent-native contract; exposing a "stream" tool would re-leak the polling/streaming dichotomy the packaging is meant to hide. |
| `POST` | `/v1/tasks/{id}/cancel` | ✅ | `task.cancel`（1:1）| `task:cancel` | ✅ | Wave B | agent-initiated cancellation; honors `cancel_outcome` payload |
| `GET` | `/v1/tasks` | ✅ | `task.list`（1:1）| `task:read` | ✅ | Wave B | inspection / debug |

### §2.6 Prompt Preview (api-shape §5.6)

| Method | Path | MCP | Tool / Packaging | Scope | M3 status | Tool ticket | Reason |
|---|---|---|---|---|---|---|---|
| `POST` | `/v1/prompt/preview` | ✅ | `prompt.preview`（1:1）| `character:read` | ✅ | Wave B | non-mutating preview; agent uses it to inspect the reconciled final prompt before committing |

### §2.7 Usage / Quota (api-shape §5.7)

| Method | Path | MCP | Tool / Packaging | Scope | M3 status | Tool ticket | Reason |
|---|---|---|---|---|---|---|---|
| `GET` | `/v1/usage/me` | ✅ | `usage.me`（1:1）| `usage:read` | 🟡 M4 | M4-future | soft quota visibility |

### §2.8 Signed URL (api-shape §5.8)

| Method | Path | MCP | Tool / Packaging | Scope | M3 status | Tool ticket | Reason |
|---|---|---|---|---|---|---|---|
| `GET` | `/storage/{key:path}` | ❌ | n/a | n/a | ✅ | n/a | Binary serving with independent signed-URL JWT (decoupled from OAuth per agent-interface Q6 / auth Q5). Agents read `storage_url` fields from tool results and HTTP-fetch them directly. |

### §2.9 Health / Meta (api-shape §5.9)

| Method | Path | MCP | Tool / Packaging | Scope | M3 status | Tool ticket | Reason |
|---|---|---|---|---|---|---|---|
| `GET` | `/health` | ❌ | n/a | n/a | ✅ | n/a | Ops only — DevOps liveness probe. |
| `GET` | `/v1/meta` | ✅ | `meta.get`（1:1）+ `degraded_services` surfaced via MCP `tools/list` extension（per §5 below）| no scope（public）| ✅ | Wave B | agent-readable model / preset metadata; degraded state must reach `tools/list` so agents can self-defer |

### §2.10 Auth (api-shape §2)

| Method | Path | MCP | Tool / Packaging | Scope | M3 status | Tool ticket | Reason |
|---|---|---|---|---|---|---|---|
| `POST` | `/v1/auth/login` | ❌ | n/a | n/a | ✅ | n/a | Username/password is a human-only flow (SPA); agent OAuth uses Authentik client credentials / auth-code+PKCE delegation. |
| `POST` | `/v1/auth/refresh` | ❌ | n/a | n/a | ✅ | n/a | Token lifecycle handled by OAuth provider for agents; this endpoint serves the legacy JWT path. |
| `POST` | `/v1/auth/logout` | ❌ | n/a | n/a | ✅ | n/a | Human session termination; agents drop tokens client-side. |
| `GET` | `/v1/auth/me` | ❌ | n/a | n/a | ✅ | n/a | Identity introspection — agents already know their client identity via the token they minted. (If a future use case needs it, surface as `auth.whoami` and revisit.) |

### §2.11 Webhooks (api-shape §3.4)

| Method | Path | MCP | Tool / Packaging | Scope | M3 status | Tool ticket | Reason |
|---|---|---|---|---|---|---|---|
| `POST` | `/v1/webhooks` | ❌（deferred）| n/a | n/a | ❌ not implemented | Phase 2 | api-shape §3.4 specs the schema, but `scope.md §3` explicitly defers webhooks to Phase 2. MCP `progress` notifications cover the agent need for now. |

---

## §3. Packaged tool → bundles map

Reverse lookup: for each packaged tool, the list of REST endpoints it consumes. Wave B tickets copy this verbatim into the tool's `bundles=[...]` field per `oauth-mcp-integration.md §3.2`.

> **Tool scopes vs endpoint scopes.** A packaged tool's `scopes` is the **union of every bundled endpoint's canonical scope**, where the bundle is the full list of REST endpoints the tool calls — **including internal helpers like `GET /v1/tasks/{id}` for SSE polling**. List the polling endpoint explicitly in `bundles=[...]` and let the union derive naturally; T-081 CI guardrail 2 (`tool.scopes ⊆ union of bundled endpoint scopes`) then stays consistent.
>
> The underlying REST endpoints themselves stay at canonical Q3 scope — `POST /v1/.../checkpoints`, `POST /v1/characters/{id}/aliases`, `POST /v1/bases|aliases/{id}/motions` are all `character:write` only. Don't propagate the tool union back onto the REST `require_scope` table; a REST client holding `character:write` without `task:read` must still be able to enqueue (they'll poll the task on their own).

### `character.create`（T-084）

```python
bundles = [
    "POST /v1/characters",                                        # step 1: create character + session
    "POST /v1/creation-sessions/{session_id}/reference-images",   # step 2 (reference mode only): upload refs FIRST, get reference_image_ids
    "POST /v1/creation-sessions/{session_id}/checkpoints",        # step 3: enqueue checkpoint (passes reference_image_ids if reference mode)
    "GET  /v1/tasks/{task_id}",                                   # internal: poll the checkpoint task until done (also SSE-equivalent)
    "POST /v1/creation-sessions/{session_id}/select-base",        # step 4: lock the checkpoint as Base
]
scopes = ["character:write", "task:read"]   # union of bundle endpoint scopes per oauth-mcp-integration.md §3.4
```

Rationale: api-shape §9 "建立 Character (模式 A / B)" flow is exactly these 4 endpoints in this **sequence** — reference-images must upload **before** checkpoints so the latter receives the populated `reference_image_ids` field. Reversing them makes reference-mode creation either fail validation or run the generation without the intended images. Agent saying "create character" expects a single returned `Character` (with `base` locked), not a 4-step orchestration burden.

### `alias.add`（T-085）

```python
bundles = [
    "POST /v1/characters/{character_id}/aliases/masks",  # step 1 (inpaint required / mixed optional): upload mask FIRST, get mask_id
    "POST /v1/characters/{character_id}/aliases",        # step 2: create alias; body carries { mask: { mask_id } } when applicable
    "GET  /v1/tasks/{task_id}",                          # internal: poll the alias-generation task until done
]
scopes = ["character:write", "task:read"]   # union of bundle endpoint scopes per oauth-mcp-integration.md §3.4
```

Rationale: alias creation has 4 input modes (`text` / `image` / `inpaint` / `mixed`). The alias-create endpoint is always hit; the mask upload (character-scoped) is **required** for `inpaint` and **optional** for `mixed` — when an agent calls `alias.add(input_mode='mixed', mask_file=<bytes>)` the tool must still upload and bind the mask, or the resulting alias drops the mask signal entirely. `alias_service._validate_input_mode_matrix` and T-085's `mask_file: bytes | None = None` field both treat the mask the same way for these two modes.

**Sequence is load-bearing:** when a `mask_file` is supplied, the tool must upload it **first** to get `mask_id`, then embed `{ mask: { mask_id } }` in the alias-create body (per `app/schemas/prompt.py::MaskInput`, which is the wire-level contract for the `mask` field). Reversing them either drops the mask signal or 422s the create request for inpaint mode. One packaged tool with a polymorphic `input_mode` argument absorbs the dispatch — agent gives the source bytes + mode and gets an `Alias`.

> **Reference-image constraint for `image` / `mixed` modes:** Phase 1 has **no** way to upload a brand-new reference image at alias time — `/v1/creation-sessions/{id}/reference-images` requires `session.status == "in_progress"` and alias creation runs only after the Base is locked (session is `completed`). Agents calling `alias.add(input_mode='image' | 'mixed')` must pass `reference_image_ids` that were uploaded **during** the original Base creation session. The packaged tool's input schema must document this constraint and reject calls that try to inline new image bytes for these modes. See §6 Q-D7 for the backend gap and recommended M4 work.

### `motion.generate`（T-086）

```python
bundles = [
    "POST /v1/bases/{base_id}/motions",       # parent_type='base'
    "POST /v1/aliases/{alias_id}/motions",    # parent_type='alias'
    "GET  /v1/tasks/{task_id}",               # internal: poll the i2v task until done (30–120s)
]
scopes = ["character:write", "task:read"]   # union of bundle endpoint scopes per oauth-mcp-integration.md §3.4
```

Rationale: polymorphic on `parent_type`. Single tool dispatches to the right endpoint, then waits on task SSE → MCP progress → returns `Motion`. Two endpoints into one tool because to the agent it's "generate motion for this character part" — `base` vs `alias` is implementation detail.

### `character.export`（M4-future）

```python
bundles = [
    "GET /v1/characters/{character_id}/export",  # 202 → task_id, export_id
    "GET /v1/tasks/{task_id}",                   # poll until completed
    # signed URL from completed task result is fetched out-of-band by the agent
]
scopes = ["character:read", "task:read"]
```

Rationale: ZIP export is async (Veo-tier latency potential for large characters). One tool packs trigger + wait + signed-URL resolution; agent gets a ready-to-fetch URL.

> **Out of M3.5b scope.** Listed here so the M4 ticket can copy the bundles verbatim from day 1. Per `scope.md §1` and `STATUS.md` Sprint 4 plan.

---

## §4. Blacklisted endpoints (consolidated)

Repeated from §2 for one-stop reading:

| Endpoint | Bucket (§1) | Reason |
|---|---|---|
| `GET /health` | Ops | DevOps liveness; agents have no use. |
| `POST /v1/auth/login` | Auth | Human-only flow. |
| `POST /v1/auth/refresh` | Auth | Token lifecycle handled by OAuth provider for agents. |
| `POST /v1/auth/logout` | Auth | Human session termination. |
| `GET /v1/auth/me` | Auth | Agents know their identity from minted token. |
| `GET /v1/exports/{id}/download` | Pure-UI redirect | 302 → signed URL; agent fetches directly. |
| `GET /storage/{key:path}` | Storage serving | Binary I/O with independent signed-URL JWT. |
| `POST /v1/webhooks` | Phase 2 defer | Not implemented; MCP `progress` covers M3.5 needs. |

---

## §5. `/v1/meta` handling

`/v1/meta` is whitelisted as a 1:1 tool (`meta.get`), but **per `scope.md §4` 互動表** the `degraded_services` array must additionally surface in MCP `tools/list` so agents can read it without an explicit `meta.get` call.

Implementation note (left to T-080 / T-081 ticket execution):
- MCP `tools/list` response carries an extension field (e.g., `_meta.degraded_services`) mirroring the same Redis-aggregated state `GET /v1/meta` reads. Agent sees the same `degraded_services` array (same schema as api-shape §5.9) and can self-defer or surface to its caller.
- `meta.get` tool stays as a 1:1 wrap for the full payload (models, preset_motions, platform_constraints_version, etc.).

This is the only api-shape endpoint with **two** MCP surfaces (a tool **and** a transport-level extension).

---

## §6. Drift / 待決 / open items

Items flagged during T-083 enumeration. Each must be resolved before the corresponding Wave B ticket lands; otherwise its tool count / bundle list is off.

### Q-D1. `GET /v1/checkpoints/{id}` — code exists, api-shape spec missing

- **Where:** `api/app/api/routes/checkpoints.py:34`
- **What:** Returns full `Checkpoint` DTO for a given id. Used by the SPA resume flow when the user reloads `/characters/new/session/{session_id}` and the client needs to refetch a checkpoint by id.
- **Mapping decision (§2.2):** 1:1 wrap as `character.get_checkpoint` with `character:read` scope.
- **Effect on T-084:** T-084 ticket says "9 個 tool = 1 packaged + 8 CRUD" — adding `character.get_checkpoint` makes it 10 (1 packaged + 9 CRUD). T-084 should either (a) accept the +1 count, or (b) explicitly defer this tool to a follow-up. **Recommendation: accept the +1** — checkpoints are a first-class agent inspection surface (an agent fork flow legitimately needs to refetch a checkpoint by id).
- **api-shape follow-up:** Open a separate `docs` ticket to add this endpoint to api-shape §5.2 (don't change spec inside T-083 per ticket Notes).

### Q-D2. `POST /v1/characters/{id}/aliases/masks` — code exists, api-shape spec missing

- **Where:** `api/app/api/routes/aliases.py:76`
- **What:** Upload a mask PNG, returns `{ mask_id, url }`. Owned by the character (not by a session — alias creation runs after Base is locked).
- **Mapping decision (§2.3):** Bundled inside `alias.add` — **required** for `input_mode='inpaint'`, **optional** for `input_mode='mixed'` (per `_validate_input_mode_matrix` + T-085 schema; see §2.3 row and §3 bundle annotation for the locked semantics). No separate tool — agent sends mask bytes as part of the `alias.add(input_mode='inpaint' | 'mixed', mask_file=<bytes>)` call; the packaged tool uploads internally.
- **Effect on T-085:** T-085 ticket says "5 個 tool = 1 packaged + 4 CRUD". This drift does **not** change that count (no separate tool added). T-085 must remember to internally call this endpoint when `input_mode='inpaint'` (required) or when `input_mode='mixed'` and `mask_file` is supplied (optional).
- **api-shape follow-up:** Add this endpoint to api-shape §5.3 in the same follow-up ticket as Q-D1.

### Q-D3. T-084 tool count reconciliation

- T-084 in `STATUS.md` Sprint 3.5b table says "9 個 tool = 1 packaged + 8 CRUD". The §2.1 + §2.2 enumeration above contributes:
  - 1 packaged (`character.create`)
  - 5 CRUD from §2.1 (`list` / `get` / `rename` / `delete` / `restore` — `manifest` / `copy` / `export` are M4-future)
  - 4 CRUD from §2.2 (`get_session` / `abandon_session` / `fork` / `get_checkpoint` ← drift per Q-D1)
  - = **1 packaged + 9 CRUD = 10**
- T-084 ticket needs to update its tool-count claim from 9 to 10 (the +1 is `get_checkpoint`). Recommendation: accept the +1 and land `get_checkpoint` in the same PR (the CRUD wrap is trivial relative to the packaged-tool work).
- Surfacing this in the T-083 PR description so the user can decide before T-084 starts.

### Q-D4. T-085 tool count (confirmation, not drift)

- T-085 in `STATUS.md` Sprint 3.5b table says "5 個 tool = 1 packaged + 4 CRUD".
- §2.3 enumeration: 1 packaged (`alias.add`) + 4 CRUD (`list` / `get` / `rename` / `delete`) = **5**. ✅ Matches.

### Q-D5. T-086 tool count (confirmation, not drift)

- T-086 in `STATUS.md` Sprint 3.5b table says "6 個 tool = 1 packaged + 5 CRUD".
- §2.4 enumeration: 1 packaged (`motion.generate`) + 5 CRUD (`list_for_base` / `list_for_alias` / `get` / `rename` / `delete`) = **6**. ✅ Matches.

### Q-D6. `task.cancel` / `task.list` / `task.get` / `prompt.preview` / `meta.get` — Wave B miscellany owner

- §2.5 / §2.6 / §2.9 list these 5 tools as 1:1 wraps but they are **not** owned by T-084 / T-085 / T-086 (verified against `STATUS.md` Sprint 3.5b table — only T-084 / T-085 / T-086 / T-087 are listed for Wave B).
- **Resolution (T-083 recommends):** open a new ticket **T-088 "Wave B miscellany — task / prompt / meta CRUD"** in the Sprint 3.5b table. Five 1:1 wraps, no packaging, est S. Sequencing: Depends on T-080 + T-081 (registry); no internal dep on T-084 / T-085 / T-086, can run in parallel with them.
- **Why not extend T-084:** the task / prompt / meta tools are cross-domain and would inflate T-084's already-grown scope (now 10 tools post-Q-D1). Keeping them in a dedicated mini-ticket preserves T-084's "character bootstrap" cohesion.
- **STATUS.md placeholder:** `STATUS.md` Sprint 3.5b table updated in this PR to include a T-088 row marked TODO, so the gap is visible at the source of truth. The ticket file itself (`tickets/T-088-*.md`) is **not** created in this PR (out of scope — T-083 is a planning doc only); to be filed by the user / next session as a follow-up before Wave B starts.
- **Effect on Wave B sequencing:** T-088 can land before or alongside T-084 / T-085 / T-086 since the 5 tools have zero inter-tool dependency on the packaged-tool work.

### Q-D7. Backend gap — no character-scoped reference image upload endpoint

- **Surfaced by:** Codex PR #108 review (P1 inline comment at this doc's `alias.add` mapping).
- **The constraint:** `POST /v1/creation-sessions/{id}/reference-images` is **session-scoped** and rejects non-`in_progress` sessions via `assert_session_writable` (`api/app/services/checkpoint_service.py:96-98`). After `select-base` locks the Base, the session is `completed`, so this endpoint is unusable from alias-creation context.
- **What the SPA does today:** Calls `POST /v1/characters/{characterId}/reference-images` (`web/src/api/endpoints/aliases.ts:83` → `uploadCharacterReference`), wired into `AliasEditPage.tsx:178`. **No backend route by that path exists** in `api/app/api/routes/` (grep verified). Either the SPA function is dead code, or it 404s in production when the user tries to attach a new reference at alias time. Out of T-083 scope to investigate / fix; a separate bug ticket should pick it up.
- **What `alias.add` can do today:** `image` / `mixed` modes accept `reference_image_ids` only — and those ids must belong to the **Base's source creation session** (per `alias_service._resolve_reference_keys` doc string: "Phase 1 has no separate alias reference upload endpoint — refs piggyback on the creation session that made the Base"). Agents cannot upload brand-new images for these modes in Phase 1.
- **Recommended M4 work:** Add `POST /v1/characters/{id}/reference-images` (character-scoped, mirroring the masks endpoint) so the packaged tool can accept inline new references. Open an M4 ticket with three deliverables: (a) backend route + service, (b) update §2 + §3 of this doc, (c) wire SPA `uploadCharacterReference` to the real endpoint (or remove the dead function).
- **Effect on T-085:** Tool input schema must reject inline image bytes for `image` / `mixed` modes until Q-D7 is fixed. The packaged tool still works for `text` and `inpaint` modes (mask uses character-scoped endpoint, which **does** exist).

---

## §7. Maintenance contract

- Any PR that adds, removes, or changes a path under `api/app/api/routes/` **must** update §2 in the same PR. This is enforced by process today; T-081 may add a CI lint that diffs §2 against the actual route tree (non-blocking warning per T-083 Notes — hard fail would require parsing markdown tables which is fragile).
- Scope changes (renaming the 5 canonical scope strings, adding new scopes) require a coordinated update to `auth/open-questions.md` §「Q3 canonical scope 字串」, `app/auth/scopes.py`, `app/auth/mcp_clients.py`, **and** §2 of this doc. See `auth/open-questions.md` §「Q3 …」 for the lock chain.
- Adding a new packaged tool requires a new entry in §3 with its `bundles` list.

---

## §8. References

- `open-questions.md` — Round 1 (Q1-Q4) / Round 2 (Q5-Q7) / Round 3 (Q8-Q9) decision log
- `scope.md` §4 — Phase 1 ↔ M3.5 interaction table; §1 — M3.5 scope vs M4 split
- `../backend/api-shape.md` §2 / §5 — REST spec being enumerated here
- `../backend/oauth-mcp-integration.md` §2 (scope `Depends`), §3.2 (`MCPTool` schema), §3.3 (packaging rule), §3.4 (scope ⊆ union)
- `../auth/open-questions.md` §「Q3 canonical scope 字串（T-053 lock）」 — the 5 canonical scope strings
