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
 * Wrap a regular Authentik authorize URL in a source-init redirect so the
 * user lands directly on the upstream IdP (e.g. Google) instead of the
 * identification stage. Authentik's `/source/oauth/login/<slug>/` view
 * stashes `next` in the session, kicks off the source's OAuth flow, and
 * (after IdP callback + user match) redirects back to `next`, where the
 * normal Auth Code + PKCE handoff resumes. PKCE verifier / state are
 * unchanged from the password path — same `stashPkceState` already
 * happened in the caller before this URL is consumed.
 *
 * The source-init path lives at the same `/oauth/` mount as the authorize
 * endpoint, so we derive the prefix by stripping the trailing
 * `/application/o/authorize/` segment from `authorizeUrl`. This avoids
 * adding a second env var for the source-init base while staying robust
 * to deployments that move Authentik off the default `/oauth/` prefix.
 *
 * Returns `null` when `sourceSlug` is empty — the caller is expected to
 * hide the shortcut button in that case (see `VITE_AUTHENTIK_GOOGLE_
 * SOURCE_SLUG=` in .env.example).
 */
export function buildSourceInitUrl(authorizeUrl: string, sourceSlug: string): string | null {
  const slug = sourceSlug.trim()
  if (!slug) return null
  const marker = '/application/o/authorize/'
  const idx = authorizeUrl.indexOf(marker)
  if (idx === -1) {
    throw new OauthError(
      'invalid_authorize_url',
      `buildSourceInitUrl: authorize URL "${authorizeUrl}" does not contain "${marker}"`,
    )
  }
  const prefix = authorizeUrl.slice(0, idx)
  return `${prefix}/source/oauth/login/${encodeURIComponent(slug)}/?next=${encodeURIComponent(
    authorizeUrl,
  )}`
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
