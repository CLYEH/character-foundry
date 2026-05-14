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

  it('renders Google, password, and Dev entries (no inline email/password form)', () => {
    renderLogin()
    expect(screen.getByRole('button', { name: '使用 Google 登入' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '使用帳號密碼登入' })).toBeInTheDocument()
    const dev = screen.getByRole('link', { name: /Authentik 管理介面/ })
    expect(dev).toHaveAttribute('href', '/oauth/if/admin/')
    expect(dev).toHaveAttribute('target', '_blank')
    expect(dev.getAttribute('rel') ?? '').toMatch(/noopener/)
    expect(screen.queryByLabelText('Email')).toBeNull()
    expect(screen.queryByLabelText('密碼')).toBeNull()
  })

  it('clicking Google redirects through the source-init URL with the authorize URL as next', async () => {
    renderLogin()
    fireEvent.click(screen.getByRole('button', { name: '使用 Google 登入' }))

    await waitFor(() => expect(assignSpy).toHaveBeenCalledTimes(1))
    // Test stubEnv hosts Authentik at https://authentik.test (no /oauth/
    // prefix), so the helper produces /source/oauth/login/google/ off the
    // same origin. In production .env the prefix is /oauth/ and the
    // helper carries it through identically — that variant is covered in
    // buildSourceInitUrl unit tests.
    const target = new URL(assignSpy.mock.calls[0][0] as string)
    expect(target.origin + target.pathname).toBe(
      'https://authentik.test/source/oauth/login/google/',
    )

    const next = target.searchParams.get('next')
    expect(next).not.toBeNull()
    const nextUrl = new URL(next!)
    expect(nextUrl.origin + nextUrl.pathname).toBe(
      'https://authentik.test/application/o/authorize/',
    )
    expect(nextUrl.searchParams.get('code_challenge_method')).toBe('S256')
    expect(nextUrl.searchParams.get('client_id')).toBe('character-foundry-spa')

    // PKCE must already be stashed by the time we hand off to Authentik —
    // the source-init redirect leaves origin, so any post-hop write would
    // be too late.
    expect(sessionStorage.getItem('cf-oauth-pkce-verifier')).toMatch(/^[A-Za-z0-9_-]{43,}$/)
    expect(sessionStorage.getItem('cf-oauth-state')).toBe(nextUrl.searchParams.get('state'))
  })

  it('clicking password goes straight to /application/o/authorize/ (no source-init hop)', async () => {
    renderLogin()
    fireEvent.click(screen.getByRole('button', { name: '使用帳號密碼登入' }))

    await waitFor(() => expect(assignSpy).toHaveBeenCalledTimes(1))
    const target = new URL(assignSpy.mock.calls[0][0] as string)
    expect(target.origin + target.pathname).toBe('https://authentik.test/application/o/authorize/')
    expect(target.searchParams.get('code_challenge_method')).toBe('S256')

    // Same PKCE invariant as the Google path.
    expect(sessionStorage.getItem('cf-oauth-pkce-verifier')).toMatch(/^[A-Za-z0-9_-]{43,}$/)
    expect(sessionStorage.getItem('cf-oauth-state')).toBe(target.searchParams.get('state'))
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
