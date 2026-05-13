import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AUTH_STORAGE_KEY, signOutServer, useAuthStore, type AuthUser } from './authStore'

const sampleUser: AuthUser = {
  id: '11111111-1111-1111-1111-111111111111',
  name: 'Leo',
  email: 'leo@example.com',
  team_id: '22222222-2222-2222-2222-222222222222',
  created_at: '2026-04-24T00:00:00Z',
}

const resetStore = () => {
  useAuthStore.setState({
    accessToken: null,
    refreshToken: null,
    idToken: null,
    user: null,
    expiresAt: null,
    tokenSource: null,
  })
  localStorage.removeItem(AUTH_STORAGE_KEY)
}

interface FetchCall {
  url: string
  method: string
  body: string
  contentType: string | null
  auth: string | null
}

function installFetchMock(handler: (call: FetchCall) => Response | Promise<Response>) {
  const calls: FetchCall[] = []
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString()
    const headers = new Headers(init?.headers)
    const body = init?.body
    const bodyText =
      typeof body === 'string' ? body : body instanceof URLSearchParams ? body.toString() : ''
    calls.push({
      url,
      method: (init?.method ?? 'GET').toUpperCase(),
      body: bodyText,
      contentType: headers.get('Content-Type'),
      auth: headers.get('Authorization'),
    })
    return handler({
      url,
      method: (init?.method ?? 'GET').toUpperCase(),
      body: bodyText,
      contentType: headers.get('Content-Type'),
      auth: headers.get('Authorization'),
    })
  })
  vi.stubGlobal('fetch', mock)
  return calls
}

describe('authStore.setAuth', () => {
  beforeEach(resetStore)

  it('sets tokens, user, source, expiry, and optional idToken', () => {
    const before = Date.now()
    useAuthStore.getState().setAuth({
      accessToken: 'a1',
      refreshToken: 'r1',
      user: sampleUser,
      expiresIn: 900,
      tokenSource: 'oauth',
      idToken: 'id1',
    })
    const s = useAuthStore.getState()
    expect(s.accessToken).toBe('a1')
    expect(s.refreshToken).toBe('r1')
    expect(s.idToken).toBe('id1')
    expect(s.user).toEqual(sampleUser)
    expect(s.tokenSource).toBe('oauth')
    expect(s.expiresAt).not.toBeNull()
    expect(s.expiresAt!).toBeGreaterThanOrEqual(before + 900 * 1000 - 50)
  })

  it('persists state (including tokenSource) to localStorage under cf-auth', () => {
    useAuthStore.getState().setAuth({
      accessToken: 'a1',
      refreshToken: 'r1',
      user: sampleUser,
      expiresIn: 900,
      tokenSource: 'jwt',
    })
    const raw = localStorage.getItem(AUTH_STORAGE_KEY)
    expect(raw).not.toBeNull()
    const persisted = JSON.parse(raw!) as {
      state: { accessToken: string; refreshToken: string; tokenSource: string }
    }
    expect(persisted.state.accessToken).toBe('a1')
    expect(persisted.state.refreshToken).toBe('r1')
    expect(persisted.state.tokenSource).toBe('jwt')
  })

  it('logout() clears every auth field', () => {
    useAuthStore.getState().setAuth({
      accessToken: 'a1',
      refreshToken: 'r1',
      user: sampleUser,
      expiresIn: 900,
      tokenSource: 'oauth',
      idToken: 'id1',
    })
    useAuthStore.getState().logout()
    const s = useAuthStore.getState()
    expect(s.accessToken).toBeNull()
    expect(s.refreshToken).toBeNull()
    expect(s.idToken).toBeNull()
    expect(s.user).toBeNull()
    expect(s.expiresAt).toBeNull()
    expect(s.tokenSource).toBeNull()
  })

  it('updateAccessToken() rotates only the access token', () => {
    useAuthStore.getState().setAuth({
      accessToken: 'a1',
      refreshToken: 'r1',
      user: sampleUser,
      expiresIn: 900,
      tokenSource: 'jwt',
    })
    useAuthStore.getState().updateAccessToken('a2', 60)
    const s = useAuthStore.getState()
    expect(s.accessToken).toBe('a2')
    expect(s.refreshToken).toBe('r1')
    expect(s.user).toEqual(sampleUser)
  })
})

describe('authStore.refresh — dual-stack', () => {
  beforeEach(resetStore)

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('jwt session hits /v1/auth/refresh and rotates only the access token', async () => {
    useAuthStore.getState().setAuth({
      accessToken: 'a1',
      refreshToken: 'r-jwt',
      user: sampleUser,
      expiresIn: 1,
      tokenSource: 'jwt',
    })
    const calls = installFetchMock(
      () =>
        new Response(JSON.stringify({ access_token: 'a2', expires_in: 600 }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
    )

    const ok = await useAuthStore.getState().refresh()

    expect(ok).toBe(true)
    expect(calls).toHaveLength(1)
    expect(calls[0].url).toContain('/v1/auth/refresh')
    expect(calls[0].method).toBe('POST')
    expect(JSON.parse(calls[0].body)).toEqual({ refresh_token: 'r-jwt' })
    const s = useAuthStore.getState()
    expect(s.accessToken).toBe('a2')
    expect(s.refreshToken).toBe('r-jwt')
    expect(s.tokenSource).toBe('jwt')
  })

  it('oauth session hits Authentik token endpoint with refresh_token grant', async () => {
    useAuthStore.getState().setAuth({
      accessToken: 'a1',
      refreshToken: 'r-oauth',
      user: sampleUser,
      expiresIn: 1,
      tokenSource: 'oauth',
    })
    const calls = installFetchMock(
      () =>
        new Response(
          JSON.stringify({
            access_token: 'a2',
            refresh_token: 'r-oauth-2',
            expires_in: 600,
            token_type: 'Bearer',
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
    )

    const ok = await useAuthStore.getState().refresh()

    expect(ok).toBe(true)
    expect(calls).toHaveLength(1)
    expect(calls[0].url).toContain('/application/o/token/')
    expect(calls[0].contentType).toContain('application/x-www-form-urlencoded')
    const params = new URLSearchParams(calls[0].body)
    expect(params.get('grant_type')).toBe('refresh_token')
    expect(params.get('refresh_token')).toBe('r-oauth')
    const s = useAuthStore.getState()
    expect(s.accessToken).toBe('a2')
    expect(s.refreshToken).toBe('r-oauth-2')
    expect(s.tokenSource).toBe('oauth')
  })

  it('returns false (and leaves state untouched) when the session rotated mid-flight', async () => {
    useAuthStore.getState().setAuth({
      accessToken: 'a1',
      refreshToken: 'r-jwt',
      user: sampleUser,
      expiresIn: 1,
      tokenSource: 'jwt',
    })
    installFetchMock(async () => {
      // Simulate a logout that lands while the refresh response is in flight.
      useAuthStore.getState().logout()
      return new Response(JSON.stringify({ access_token: 'a2', expires_in: 600 }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    })

    const ok = await useAuthStore.getState().refresh()

    expect(ok).toBe(false)
    expect(useAuthStore.getState().accessToken).toBeNull()
  })

  it('returns false when refresh upstream is non-2xx', async () => {
    useAuthStore.getState().setAuth({
      accessToken: 'a1',
      refreshToken: 'r-jwt',
      user: sampleUser,
      expiresIn: 1,
      tokenSource: 'jwt',
    })
    installFetchMock(() => new Response(null, { status: 401 }))

    const ok = await useAuthStore.getState().refresh()

    expect(ok).toBe(false)
    expect(useAuthStore.getState().accessToken).toBe('a1')
  })

  it('returns false immediately when there is no refresh token', async () => {
    const fetchSpy = vi.fn()
    vi.stubGlobal('fetch', fetchSpy)
    const ok = await useAuthStore.getState().refresh()
    expect(ok).toBe(false)
    expect(fetchSpy).not.toHaveBeenCalled()
  })
})

describe('signOutServer', () => {
  beforeEach(resetStore)

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('oauth session POSTs the refresh token to Authentik revoke', async () => {
    useAuthStore.getState().setAuth({
      accessToken: 'a1',
      refreshToken: 'r-oauth',
      user: sampleUser,
      expiresIn: 900,
      tokenSource: 'oauth',
    })
    const calls = installFetchMock(() => new Response(null, { status: 200 }))

    await signOutServer()

    expect(calls).toHaveLength(1)
    const params = new URLSearchParams(calls[0].body)
    expect(params.get('token')).toBe('r-oauth')
    expect(params.get('token_type_hint')).toBe('refresh_token')
    expect(calls[0].url).toContain('/revoke/')
  })

  it('jwt session POSTs /v1/auth/logout with the refresh token', async () => {
    useAuthStore.getState().setAuth({
      accessToken: 'a1',
      refreshToken: 'r-jwt',
      user: sampleUser,
      expiresIn: 900,
      tokenSource: 'jwt',
    })
    const calls = installFetchMock(() => new Response(null, { status: 200 }))

    await signOutServer()

    expect(calls).toHaveLength(1)
    expect(calls[0].url).toContain('/v1/auth/logout')
    expect(JSON.parse(calls[0].body)).toEqual({ refresh_token: 'r-jwt' })
  })

  it('no-ops when there is no refresh token', async () => {
    const fetchSpy = vi.fn()
    vi.stubGlobal('fetch', fetchSpy)
    await signOutServer()
    expect(fetchSpy).not.toHaveBeenCalled()
  })
})
