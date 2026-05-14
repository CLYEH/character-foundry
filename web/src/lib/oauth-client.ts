import { authentik } from '@/config'

const PKCE_VERIFIER_KEY = 'cf-oauth-pkce-verifier'
const STATE_KEY = 'cf-oauth-state'
const REDIRECT_BACK_KEY = 'cf-oauth-redirect-back'

export const REDIRECT_PATH = '/auth/callback'

/**
 * Reject anything that isn't a same-origin internal path. Used at both the
 * producer (login page) and the consumer (auth callback) so a poisoned
 * sessionStorage entry can't open-redirect us off-origin even though the
 * only writer today is the login page.
 */
export function isSafeInternalPath(value: string | null): value is string {
  return typeof value === 'string' && value.startsWith('/') && !value.startsWith('//')
}

export interface OauthTokenResponse {
  access_token: string
  refresh_token?: string
  id_token?: string
  expires_in: number
  token_type: string
}

export class OauthError extends Error {
  code: string
  constructor(code: string, message: string) {
    super(message)
    this.name = 'OauthError'
    this.code = code
  }
}

function base64UrlEncode(bytes: Uint8Array): string {
  let bin = ''
  for (let i = 0; i < bytes.length; i += 1) bin += String.fromCharCode(bytes[i])
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

function randomBytes(length: number): Uint8Array {
  const bytes = new Uint8Array(length)
  crypto.getRandomValues(bytes)
  return bytes
}

export function generateVerifier(): string {
  // 32 bytes → 43 char base64url, comfortably inside RFC 7636's 43–128 range.
  return base64UrlEncode(randomBytes(32))
}

export async function computeChallenge(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier))
  return base64UrlEncode(new Uint8Array(digest))
}

export function generateState(): string {
  return base64UrlEncode(randomBytes(16))
}

export function stashPkceState(verifier: string, state: string, redirectBack: string | null): void {
  sessionStorage.setItem(PKCE_VERIFIER_KEY, verifier)
  sessionStorage.setItem(STATE_KEY, state)
  if (redirectBack) sessionStorage.setItem(REDIRECT_BACK_KEY, redirectBack)
  else sessionStorage.removeItem(REDIRECT_BACK_KEY)
}

export function consumePkceState(): {
  verifier: string | null
  state: string | null
  redirectBack: string | null
} {
  const verifier = sessionStorage.getItem(PKCE_VERIFIER_KEY)
  const state = sessionStorage.getItem(STATE_KEY)
  const redirectBack = sessionStorage.getItem(REDIRECT_BACK_KEY)
  sessionStorage.removeItem(PKCE_VERIFIER_KEY)
  sessionStorage.removeItem(STATE_KEY)
  sessionStorage.removeItem(REDIRECT_BACK_KEY)
  return { verifier, state, redirectBack }
}

export function callbackRedirectUri(): string {
  return `${window.location.origin}${REDIRECT_PATH}`
}

export function buildAuthorizeUrl(opts: { challenge: string; state: string }): string {
  const params = new URLSearchParams({
    response_type: 'code',
    client_id: authentik.clientId,
    redirect_uri: callbackRedirectUri(),
    code_challenge: opts.challenge,
    code_challenge_method: 'S256',
    state: opts.state,
    scope: authentik.scopes,
  })
  return `${authentik.authorizeUrl}?${params.toString()}`
}

/**
 * The Authentik flow that fronts the SPA's "Sign in with Google" button.
 * Defined declaratively in infra/authentik/blueprints/cf-google-init.yaml.
 */
const GOOGLE_INIT_FLOW_SLUG = 'cf-google-init'

/**
 * Wrap a regular Authentik authorize URL so the user lands directly on
 * the upstream IdP (e.g. Google) instead of the identification stage,
 * while preserving the post-login redirect back to `authorizeUrl`. PKCE
 * verifier / state are unchanged from the password path — the same
 * `stashPkceState` already happened in the caller before this URL is
 * consumed.
 *
 * We navigate to the `cf-google-init` flow executor — NOT Authentik's
 * bare `/source/oauth/login/<slug>/` view. The bare source-init view
 * silently ignores `?next=` (authentik/sources/oauth/views/redirect.py
 * never persists it anywhere), so a direct source-init hop dead-ends the
 * operator on Authentik's `/if/user/` page after the IdP callback.
 * Routing through a flow executor instead populates Authentik's
 * `SESSION_KEY_GET`, which `SourceFlowManager._prepare_flow` reads to
 * compute the post-callback redirect; the flow's single RedirectStage
 * then forwards to the source-init view. See the blueprint header +
 * T-073 for the full trace.
 *
 * `next` rides as a PLAIN query param on the flow-*interface* URL. Do
 * NOT pre-wrap it in `?query=`: the interface frontend already bundles
 * the whole `window.location.search` into the executor API's `?query=`
 * itself (`FlowInterface*.js`: `flowsExecutorGet({ query:
 * location.search.substring(1) })`). So `?next=X` on the interface URL
 * arrives at the executor as `?query=next=X`, which `dispatch()` parses
 * into `SESSION_KEY_GET = {next: X}`. Pre-wrapping it (`?query=next=X` —
 * what T-073 originally shipped) double-bundles to `{query: "next=X"}`,
 * the `next` key is lost, and `_prepare_flow` falls back to `/if/user/`
 * (the T-075 regression). The executor *API* path does want `?query=` —
 * but the SPA hits the interface, not the API.
 *
 * The flow interface lives at the same `/oauth/` mount as the authorize
 * endpoint, so we derive the prefix by stripping the trailing
 * `/application/o/authorize/` segment from `authorizeUrl`. This avoids a
 * second env var for the interface base while staying robust to
 * deployments that move Authentik off the default `/oauth/` prefix.
 *
 * Returns `null` when `sourceSlug` is empty — the caller hides the
 * shortcut button in that case (see `VITE_AUTHENTIK_GOOGLE_SOURCE_SLUG=`
 * in .env.example). `sourceSlug` is now only a visibility gate: the
 * upstream source is fixed by the `cf-google-init` blueprint (slug
 * `google`), not threaded through this URL.
 *
 * Trust boundary: `authorizeUrl` becomes the post-callback redirect
 * target, so it MUST stay derived from `authentik.*` config (today it's
 * always `buildAuthorizeUrl(...)` output) — never pass user- or
 * query-controlled input as `authorizeUrl`, or a hostile `next` could
 * redirect the post-IdP-callback hop off-origin. There is effectively
 * NO backstop: Authentik validates `next` (`is_url_absolute`) only on
 * the `SESSION_KEY_GET` fallback path in `_flow_done`, NOT on the
 * `PLAN_CONTEXT_REDIRECT` path that the source flow this URL launches
 * actually takes. The config-derived invariant above is load-bearing.
 */
export function buildSourceInitUrl(authorizeUrl: string, sourceSlug: string): string | null {
  if (!sourceSlug.trim()) return null
  const marker = '/application/o/authorize/'
  const idx = authorizeUrl.indexOf(marker)
  if (idx === -1) {
    throw new OauthError(
      'invalid_authorize_url',
      `buildSourceInitUrl: authorize URL "${authorizeUrl}" does not contain "${marker}"`,
    )
  }
  const prefix = authorizeUrl.slice(0, idx)
  return `${prefix}/if/flow/${GOOGLE_INIT_FLOW_SLUG}/?${new URLSearchParams({ next: authorizeUrl }).toString()}`
}

async function postForm(url: string, form: URLSearchParams): Promise<OauthTokenResponse> {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: form,
  })
  if (!res.ok) {
    let code = `HTTP_${res.status}`
    let message = res.statusText
    try {
      const body = (await res.json()) as { error?: string; error_description?: string }
      if (body.error) code = body.error
      if (body.error_description) message = body.error_description
    } catch {
      /* non-JSON error body — keep defaults */
    }
    throw new OauthError(code, message)
  }
  return (await res.json()) as OauthTokenResponse
}

export function exchangeCodeForToken(opts: {
  code: string
  verifier: string
}): Promise<OauthTokenResponse> {
  const form = new URLSearchParams({
    grant_type: 'authorization_code',
    code: opts.code,
    redirect_uri: callbackRedirectUri(),
    client_id: authentik.clientId,
    code_verifier: opts.verifier,
  })
  return postForm(authentik.tokenUrl, form)
}

export function refreshOauthToken(refreshToken: string): Promise<OauthTokenResponse> {
  const form = new URLSearchParams({
    grant_type: 'refresh_token',
    refresh_token: refreshToken,
    client_id: authentik.clientId,
  })
  return postForm(authentik.tokenUrl, form)
}

/**
 * RFC 7009 token revocation. Authentik exposes `/application/o/revoke/`
 * (configured as `VITE_AUTHENTIK_LOGOUT_URL`). We pass the refresh token
 * with an explicit `token_type_hint=refresh_token` so the AS doesn't have
 * to probe both stores. Best-effort: local state is cleared either way.
 *
 * NOTE: this is **not** Authentik's OIDC `end-session/` endpoint — that one
 * wants `id_token_hint` + a redirect dance for global session termination,
 * which is explicitly out of scope for Phase 1 (single user). See T-056
 * §"Not in scope".
 */
export async function revokeOauthToken(token: string): Promise<void> {
  if (!authentik.logoutUrl) return
  await fetch(authentik.logoutUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      token,
      token_type_hint: 'refresh_token',
      client_id: authentik.clientId,
    }),
  }).catch(() => {
    /* best-effort revoke; local state is cleared either way */
  })
}
