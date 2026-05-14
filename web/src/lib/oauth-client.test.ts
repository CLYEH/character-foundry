import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  buildAuthorizeUrl,
  buildSourceInitUrl,
  computeChallenge,
  consumePkceState,
  exchangeCodeForToken,
  generateState,
  generateVerifier,
  OauthError,
  refreshOauthToken,
  stashPkceState,
} from './oauth-client'

describe('PKCE helpers', () => {
  it('generateVerifier produces RFC 7636-compatible base64url strings', () => {
    for (let i = 0; i < 5; i += 1) {
      const v = generateVerifier()
      expect(v).toMatch(/^[A-Za-z0-9_-]+$/)
      expect(v.length).toBeGreaterThanOrEqual(43)
      expect(v.length).toBeLessThanOrEqual(128)
    }
  })

  it('two verifiers in a row differ', () => {
    expect(generateVerifier()).not.toBe(generateVerifier())
  })

  it('computeChallenge matches a known SHA-256(verifier) base64url digest', async () => {
    // Vector from RFC 7636 Appendix B.
    const verifier = 'dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk'
    const expected = 'E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM'
    expect(await computeChallenge(verifier)).toBe(expected)
  })

  it('generateState is base64url and reasonably long', () => {
    const s = generateState()
    expect(s).toMatch(/^[A-Za-z0-9_-]+$/)
    expect(s.length).toBeGreaterThanOrEqual(22)
  })
})

describe('PKCE session storage round-trip', () => {
  beforeEach(() => sessionStorage.clear())

  it('stash then consume returns the same values and clears storage', () => {
    stashPkceState('v1', 's1', '/dashboard')
    const out = consumePkceState()
    expect(out).toEqual({ verifier: 'v1', state: 's1', redirectBack: '/dashboard' })
    // Second consume must be empty — single-use semantics.
    expect(consumePkceState()).toEqual({ verifier: null, state: null, redirectBack: null })
  })

  it('omits redirectBack when none was passed', () => {
    stashPkceState('v1', 's1', null)
    expect(consumePkceState().redirectBack).toBeNull()
  })
})

describe('buildAuthorizeUrl', () => {
  it('includes PKCE challenge, state, scopes, redirect, and client_id', () => {
    const original = window.location
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { ...original, origin: 'https://app.test' },
    })
    try {
      const url = new URL(buildAuthorizeUrl({ challenge: 'CHAL', state: 'ST' }))
      expect(url.origin + url.pathname).toBe('https://authentik.test/application/o/authorize/')
      expect(url.searchParams.get('response_type')).toBe('code')
      expect(url.searchParams.get('client_id')).toBe('character-foundry-spa')
      expect(url.searchParams.get('redirect_uri')).toBe('https://app.test/auth/callback')
      expect(url.searchParams.get('code_challenge')).toBe('CHAL')
      expect(url.searchParams.get('code_challenge_method')).toBe('S256')
      expect(url.searchParams.get('state')).toBe('ST')
      const scope = url.searchParams.get('scope') ?? ''
      for (const required of [
        'openid',
        'character:read',
        'character:write',
        'task:read',
        'task:cancel',
        'usage:read',
      ]) {
        expect(scope).toContain(required)
      }
    } finally {
      Object.defineProperty(window, 'location', { configurable: true, value: original })
    }
  })
})

describe('buildSourceInitUrl', () => {
  const authorize =
    '/oauth/application/o/authorize/?response_type=code&client_id=character-foundry-spa&state=ST'

  it('wraps a relative authorize URL in /oauth/source/oauth/login/<slug>/ with next=', () => {
    const url = buildSourceInitUrl(authorize, 'google')
    expect(url).not.toBeNull()
    // URL is relative so parse with a dummy base.
    const parsed = new URL(url!, 'https://app.test')
    expect(parsed.pathname).toBe('/oauth/source/oauth/login/google/')
    expect(parsed.searchParams.get('next')).toBe(authorize)
  })

  it('preserves the absolute origin when the authorize URL is absolute', () => {
    const absolute = `https://authentik.test${authorize}`
    const url = buildSourceInitUrl(absolute, 'google')
    expect(url).not.toBeNull()
    const parsed = new URL(url!)
    expect(parsed.origin + parsed.pathname).toBe(
      'https://authentik.test/oauth/source/oauth/login/google/',
    )
    expect(parsed.searchParams.get('next')).toBe(absolute)
  })

  it('returns null when the source slug is empty or whitespace (button hidden)', () => {
    expect(buildSourceInitUrl(authorize, '')).toBeNull()
    expect(buildSourceInitUrl(authorize, '   ')).toBeNull()
  })

  it('encodes slugs that contain URL-unsafe characters', () => {
    const url = buildSourceInitUrl(authorize, 'oidc/main')
    expect(url).not.toBeNull()
    const parsed = new URL(url!, 'https://app.test')
    expect(parsed.pathname).toBe('/oauth/source/oauth/login/oidc%2Fmain/')
  })

  it('throws OauthError when the authorize URL lacks the standard segment', () => {
    let caught: unknown
    try {
      buildSourceInitUrl('/oauth/some/other/path/?x=1', 'google')
    } catch (err) {
      caught = err
    }
    expect(caught).toBeInstanceOf(OauthError)
    expect((caught as OauthError).code).toBe('invalid_authorize_url')
  })
})

describe('exchangeCodeForToken + refreshOauthToken', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('exchangeCodeForToken posts form-encoded body with authorization_code grant', async () => {
    let captured: { url: string; init: RequestInit | undefined } | null = null
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        captured = { url: typeof input === 'string' ? input : input.toString(), init }
        return new Response(
          JSON.stringify({
            access_token: 'a',
            refresh_token: 'r',
            expires_in: 900,
            token_type: 'Bearer',
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        )
      }),
    )
    const result = await exchangeCodeForToken({ code: 'C', verifier: 'V' })
    expect(result.access_token).toBe('a')
    expect(captured).not.toBeNull()
    const { init } = captured!
    const headers = (init?.headers as Record<string, string>) ?? {}
    expect(headers['Content-Type']).toContain('application/x-www-form-urlencoded')
    const params = new URLSearchParams(init?.body as string)
    expect(params.get('grant_type')).toBe('authorization_code')
    expect(params.get('code')).toBe('C')
    expect(params.get('code_verifier')).toBe('V')
    expect(params.get('client_id')).toBe('character-foundry-spa')
  })

  it('throws OauthError with code + message when the token endpoint 4xxs', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(JSON.stringify({ error: 'invalid_grant', error_description: 'bad code' }), {
            status: 400,
            headers: { 'Content-Type': 'application/json' },
          }),
      ),
    )
    await expect(refreshOauthToken('r')).rejects.toMatchObject({
      name: 'OauthError',
      code: 'invalid_grant',
      message: 'bad code',
    })
  })

  it('falls back to HTTP code when the error body is not JSON', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('boom', { status: 502, statusText: 'Bad Gateway' })),
    )
    let caught: unknown
    try {
      await refreshOauthToken('r')
    } catch (err) {
      caught = err
    }
    expect(caught).toBeInstanceOf(OauthError)
    expect((caught as OauthError).code).toBe('HTTP_502')
  })
})
