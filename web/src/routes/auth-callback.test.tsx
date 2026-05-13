import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'

import AuthCallbackPage from './auth-callback'
import { useAuthStore, AUTH_STORAGE_KEY, type AuthUser } from '@/stores/authStore'

const sampleUser: AuthUser = {
  id: 'u1',
  name: 'Leo',
  email: 'leo@example.com',
  team_id: 't1',
  created_at: '2026-04-24T00:00:00Z',
}

function renderCallback(url: string) {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route path="/auth/callback" element={<AuthCallbackPage />} />
        <Route path="/" element={<div data-testid="home" />} />
        <Route path="/login" element={<div data-testid="login" />} />
        <Route path="/characters/abc" element={<div data-testid="redirected-back" />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('AuthCallbackPage', () => {
  beforeEach(() => {
    useAuthStore.setState({
      accessToken: null,
      refreshToken: null,
      idToken: null,
      user: null,
      expiresAt: null,
      tokenSource: null,
    })
    localStorage.removeItem(AUTH_STORAGE_KEY)
    sessionStorage.clear()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('exchanges code → token, fetches /me, populates authStore, and redirects', async () => {
    sessionStorage.setItem('cf-oauth-pkce-verifier', 'V')
    sessionStorage.setItem('cf-oauth-state', 'ST')
    sessionStorage.setItem('cf-oauth-redirect-back', '/characters/abc')

    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString()
      if (url.includes('/application/o/token/')) {
        return new Response(
          JSON.stringify({
            access_token: 'AT',
            refresh_token: 'RT',
            id_token: 'IDT',
            expires_in: 900,
            token_type: 'Bearer',
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        )
      }
      if (url.endsWith('/v1/auth/me')) {
        return new Response(JSON.stringify({ user: sampleUser }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      return new Response('unexpected', { status: 500 })
    })
    vi.stubGlobal('fetch', fetchMock)

    renderCallback('/auth/callback?code=CODE&state=ST')

    await waitFor(() => {
      expect(screen.getByTestId('redirected-back')).toBeInTheDocument()
    })

    const auth = useAuthStore.getState()
    expect(auth.accessToken).toBe('AT')
    expect(auth.refreshToken).toBe('RT')
    expect(auth.idToken).toBe('IDT')
    expect(auth.tokenSource).toBe('oauth')
    expect(auth.user).toEqual(sampleUser)
    // sessionStorage stash must be cleared after consume.
    expect(sessionStorage.getItem('cf-oauth-pkce-verifier')).toBeNull()
  })

  it('shows an error when the returned state does not match the stashed one', async () => {
    sessionStorage.setItem('cf-oauth-pkce-verifier', 'V')
    sessionStorage.setItem('cf-oauth-state', 'STORED')

    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    renderCallback('/auth/callback?code=CODE&state=DIFFERENT')

    await waitFor(() => {
      expect(screen.getByText(/狀態不符/)).toBeInTheDocument()
    })
    expect(fetchMock).not.toHaveBeenCalled()
    expect(useAuthStore.getState().accessToken).toBeNull()

    // 「重試」button navigates back to /login.
    fireEvent.click(screen.getByRole('button', { name: '重試' }))
    await waitFor(() => {
      expect(screen.getByTestId('login')).toBeInTheDocument()
    })
  })

  it('shows an error when the token exchange itself fails', async () => {
    sessionStorage.setItem('cf-oauth-pkce-verifier', 'V')
    sessionStorage.setItem('cf-oauth-state', 'ST')

    const fetchMock = vi.fn(
      async () =>
        new Response(
          JSON.stringify({ error: 'invalid_grant', error_description: 'code expired' }),
          { status: 400, headers: { 'Content-Type': 'application/json' } },
        ),
    )
    vi.stubGlobal('fetch', fetchMock)

    renderCallback('/auth/callback?code=CODE&state=ST')

    await waitFor(() => {
      expect(screen.getByText('code expired')).toBeInTheDocument()
    })
    expect(useAuthStore.getState().accessToken).toBeNull()
  })

  it('shows error when Authentik returns ?error= without code', async () => {
    sessionStorage.setItem('cf-oauth-pkce-verifier', 'V')
    sessionStorage.setItem('cf-oauth-state', 'ST')

    renderCallback('/auth/callback?error=access_denied&error_description=user%20cancelled')

    await waitFor(() => {
      expect(screen.getByText('user cancelled')).toBeInTheDocument()
    })
  })
})
