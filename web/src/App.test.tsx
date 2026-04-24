import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'

import App from './App'
import { AUTH_STORAGE_KEY, useAuthStore } from '@/stores/authStore'

describe('App', () => {
  beforeEach(() => {
    useAuthStore.setState({
      accessToken: null,
      refreshToken: null,
      user: null,
      expiresAt: null,
    })
    localStorage.removeItem(AUTH_STORAGE_KEY)
    window.history.replaceState({}, '', '/')
  })

  it('renders the index hello message when authenticated', () => {
    useAuthStore.setState({
      accessToken: 'a',
      refreshToken: 'r',
      user: {
        id: '11111111-1111-1111-1111-111111111111',
        name: 'Leo',
        email: 'leo@example.com',
        team_id: '22222222-2222-2222-2222-222222222222',
        created_at: '2026-04-24T00:00:00Z',
      },
      expiresAt: Date.now() + 60_000,
    })
    render(<App />)
    expect(screen.getByRole('heading', { name: /hello/i })).toBeInTheDocument()
  })
})
