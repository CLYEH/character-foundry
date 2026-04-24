import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { apiFetch, ApiError, authFailureRedirect } from './client'
import { AUTH_STORAGE_KEY, useAuthStore } from '@/stores/authStore'

interface FetchCall {
  url: string
  method: string
  auth: string | null
  body: string | null
}

function installFetchMock(handler: (call: FetchCall) => Response | Promise<Response>) {
  const calls: FetchCall[] = []
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString()
    const headers = new Headers(init?.headers)
    const call: FetchCall = {
      url,
      method: (init?.method ?? 'GET').toUpperCase(),
      auth: headers.get('Authorization'),
      body: typeof init?.body === 'string' ? init.body : null,
    }
    calls.push(call)
    return handler(call)
  })
  vi.stubGlobal('fetch', fetchMock)
  return calls
}

function seedAuth(accessToken: string | null, refreshToken: string | null) {
  useAuthStore.setState({
    accessToken,
    refreshToken,
    user: null,
    expiresAt: accessToken ? Date.now() + 60_000 : null,
  })
}

describe('apiFetch — 401 refresh flow', () => {
  beforeEach(() => {
    seedAuth('expired', 'r1')
    localStorage.removeItem(AUTH_STORAGE_KEY)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('refreshes access token on 401 and retries the original request', async () => {
    const calls = installFetchMock(async ({ url, auth }) => {
      if (url.endsWith('/v1/auth/refresh')) {
        return new Response(JSON.stringify({ access_token: 'new', expires_in: 900 }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      if (auth === 'Bearer expired') {
        return new Response(null, { status: 401 })
      }
      if (auth === 'Bearer new') {
        return new Response(JSON.stringify({ user: { id: 'u1' } }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      return new Response('unexpected', { status: 500 })
    })

    const result = await apiFetch<{ user: { id: string } }>('/v1/auth/me')

    expect(result.user.id).toBe('u1')
    expect(useAuthStore.getState().accessToken).toBe('new')

    const paths = calls.map((c) => c.url)
    expect(paths).toEqual([
      expect.stringContaining('/v1/auth/me'),
      expect.stringContaining('/v1/auth/refresh'),
      expect.stringContaining('/v1/auth/me'),
    ])
    expect(calls[0].auth).toBe('Bearer expired')
    expect(calls[2].auth).toBe('Bearer new')
  })

  it('deduplicates concurrent refreshes (single refresh, both retries succeed)', async () => {
    const calls = installFetchMock(async ({ url, auth }) => {
      if (url.endsWith('/v1/auth/refresh')) {
        // Artificial delay so both requests observe an in-flight refresh.
        await new Promise((r) => setTimeout(r, 10))
        return new Response(JSON.stringify({ access_token: 'new', expires_in: 900 }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      if (auth === 'Bearer expired') return new Response(null, { status: 401 })
      if (auth === 'Bearer new') {
        return new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      return new Response('unexpected', { status: 500 })
    })

    const [a, b] = await Promise.all([
      apiFetch<{ ok: boolean }>('/v1/a'),
      apiFetch<{ ok: boolean }>('/v1/b'),
    ])

    expect(a.ok).toBe(true)
    expect(b.ok).toBe(true)
    const refreshCalls = calls.filter((c) => c.url.endsWith('/v1/auth/refresh'))
    expect(refreshCalls).toHaveLength(1)
  })

  it('discards in-flight refresh if the session is cleared before it resolves', async () => {
    let releaseRefresh: ((value: Response) => void) | null = null
    const refreshGate = new Promise<Response>((resolve) => {
      releaseRefresh = resolve
    })

    installFetchMock(async ({ url, auth }) => {
      if (url.endsWith('/v1/auth/refresh')) {
        return refreshGate
      }
      if (auth === 'Bearer expired') return new Response(null, { status: 401 })
      return new Response('unexpected', { status: 500 })
    })

    const redirectSpy = vi.spyOn(authFailureRedirect, 'toLogin').mockImplementation(() => {})

    const pending = apiFetch('/v1/auth/me').catch((e) => e)

    // Yield so the 401 triggers the refresh and it parks on the gate.
    await new Promise((r) => setTimeout(r, 0))

    // User clicks logout mid-refresh.
    useAuthStore.getState().logout()

    // Now let the refresh complete successfully.
    releaseRefresh!(
      new Response(JSON.stringify({ access_token: 'new', expires_in: 900 }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    await pending

    // Store stays cleared — the in-flight refresh did not re-seat a token.
    expect(useAuthStore.getState().accessToken).toBeNull()
    expect(useAuthStore.getState().refreshToken).toBeNull()
    // AND the stale 401 path must not force an extra logout/redirect; the
    // user's own logout already cleared state and AppLayout will navigate.
    expect(redirectSpy).not.toHaveBeenCalled()
  })

  it('does not tear down a new session when a stale 401 resolves after re-login', async () => {
    let releaseRefresh: ((value: Response) => void) | null = null
    const refreshGate = new Promise<Response>((resolve) => {
      releaseRefresh = resolve
    })

    installFetchMock(async ({ url, auth }) => {
      if (url.endsWith('/v1/auth/refresh')) {
        return refreshGate
      }
      if (auth === 'Bearer expired') return new Response(null, { status: 401 })
      return new Response('unexpected', { status: 500 })
    })

    const redirectSpy = vi.spyOn(authFailureRedirect, 'toLogin').mockImplementation(() => {})

    const pending = apiFetch('/v1/auth/me').catch((e) => e)
    await new Promise((r) => setTimeout(r, 0))

    // User logs out and then logs in again as a fresh session while the old
    // refresh is still parked on the gate.
    useAuthStore.getState().logout()
    useAuthStore.setState({
      accessToken: 'a2',
      refreshToken: 'r2',
      user: null,
      expiresAt: Date.now() + 60_000,
    })

    // Release the old refresh with a 401 so doRefresh returns false.
    releaseRefresh!(new Response(null, { status: 401 }))

    await pending

    // New session must survive — the stale 401 from the old session must not
    // call logout() or redirect.
    expect(useAuthStore.getState().accessToken).toBe('a2')
    expect(useAuthStore.getState().refreshToken).toBe('r2')
    expect(redirectSpy).not.toHaveBeenCalled()
  })

  it('logs out and redirects to /login when refresh also fails', async () => {
    installFetchMock(async ({ url }) => {
      if (url.endsWith('/v1/auth/refresh')) return new Response(null, { status: 401 })
      return new Response(null, { status: 401 })
    })

    const redirectSpy = vi.spyOn(authFailureRedirect, 'toLogin').mockImplementation(() => {})

    await expect(apiFetch('/v1/auth/me')).rejects.toBeInstanceOf(ApiError)

    expect(useAuthStore.getState().accessToken).toBeNull()
    expect(useAuthStore.getState().refreshToken).toBeNull()
    expect(redirectSpy).toHaveBeenCalledTimes(1)
  })

  it('does not attempt refresh for skipAuth requests (e.g. login with wrong password)', async () => {
    seedAuth(null, null)
    const calls = installFetchMock(async ({ url }) => {
      if (url.endsWith('/v1/auth/login')) {
        return new Response(JSON.stringify({ error: { code: 'AUTH_INVALID_CREDENTIALS' } }), {
          status: 401,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      return new Response('unexpected', { status: 500 })
    })

    await expect(
      apiFetch('/v1/auth/login', {
        method: 'POST',
        body: JSON.stringify({ email: 'a@b.c', password: 'bad' }),
        skipAuth: true,
      }),
    ).rejects.toMatchObject({ status: 401, code: 'AUTH_INVALID_CREDENTIALS' })

    expect(calls.filter((c) => c.url.endsWith('/v1/auth/refresh'))).toHaveLength(0)
  })
})
