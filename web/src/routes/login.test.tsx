import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'

import LoginPage from './login'
import { useAuthStore, AUTH_STORAGE_KEY } from '@/stores/authStore'

function renderLogin(initialEntries: string[] = ['/login']) {
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="*" element={<div data-testid="elsewhere" />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('LoginPage', () => {
  let originalLocation: Location
  let assignSpy: ReturnType<typeof vi.fn>

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

    originalLocation = window.location
    assignSpy = vi.fn()
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { ...originalLocation, origin: 'https://app.test', assign: assignSpy },
    })
  })

  afterEach(() => {
    Object.defineProperty(window, 'location', { configurable: true, value: originalLocation })
    vi.restoreAllMocks()
  })

  it('renders only the Google sign-in button (no email/password form)', () => {
    renderLogin()
    expect(screen.getByRole('button', { name: '使用 Google 登入' })).toBeInTheDocument()
    expect(screen.queryByLabelText('Email')).toBeNull()
    expect(screen.queryByLabelText('密碼')).toBeNull()
  })

  it('clicking the button stashes PKCE state and redirects to Authentik authorize', async () => {
    renderLogin()
    fireEvent.click(screen.getByRole('button', { name: '使用 Google 登入' }))

    await waitFor(() => expect(assignSpy).toHaveBeenCalledTimes(1))
    const target = new URL(assignSpy.mock.calls[0][0] as string)
    expect(target.origin + target.pathname).toBe('https://authentik.test/application/o/authorize/')
    expect(target.searchParams.get('response_type')).toBe('code')
    expect(target.searchParams.get('client_id')).toBe('character-foundry-spa')
    expect(target.searchParams.get('redirect_uri')).toBe('https://app.test/auth/callback')
    expect(target.searchParams.get('code_challenge_method')).toBe('S256')

    const challengeFromUrl = target.searchParams.get('code_challenge')
    const stateFromUrl = target.searchParams.get('state')
    expect(challengeFromUrl).toMatch(/^[A-Za-z0-9_-]{20,}$/)
    expect(stateFromUrl).toMatch(/^[A-Za-z0-9_-]{10,}$/)

    // PKCE verifier + state must round-trip through sessionStorage for the
    // callback page to consume.
    expect(sessionStorage.getItem('cf-oauth-pkce-verifier')).toMatch(/^[A-Za-z0-9_-]{43,}$/)
    expect(sessionStorage.getItem('cf-oauth-state')).toBe(stateFromUrl)
  })

  it('stashes the redirect_back query when present so callback can return there', async () => {
    renderLogin(['/login?redirect_back=%2Fcharacters%2Fabc'])
    fireEvent.click(screen.getByRole('button', { name: '使用 Google 登入' }))
    await waitFor(() => {
      expect(sessionStorage.getItem('cf-oauth-redirect-back')).toBe('/characters/abc')
    })
  })

  it('redirects away when already authenticated', () => {
    useAuthStore.setState({ accessToken: 'a', refreshToken: 'r', tokenSource: 'oauth' })
    renderLogin()
    expect(screen.getByTestId('elsewhere')).toBeInTheDocument()
  })
})
