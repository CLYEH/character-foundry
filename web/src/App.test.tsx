import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import App from './App'
import { queryClient } from '@/api/queryClient'
import { AUTH_STORAGE_KEY, useAuthStore } from '@/stores/authStore'

describe('App', () => {
  const originalFetch = globalThis.fetch

  beforeEach(() => {
    useAuthStore.setState({
      accessToken: null,
      refreshToken: null,
      user: null,
      expiresAt: null,
    })
    localStorage.removeItem(AUTH_STORAGE_KEY)
    queryClient.clear()
    window.history.replaceState({}, '', '/')

    // The dashboard at `/` issues GET /v1/characters?owner_id=me as soon as
    // it mounts. Stub fetch so the smoke test only validates routing /
    // layout, not the network path.
    const fetchStub: typeof fetch = async (input) => {
      const url =
        typeof input === 'string'
          ? input
          : input instanceof URL
            ? input.href
            : input instanceof Request
              ? input.url
              : ''
      if (url.includes('/v1/characters')) {
        return new Response(JSON.stringify({ items: [], next_cursor: null }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      if (url.includes('/v1/meta')) {
        return new Response(
          JSON.stringify({
            models: { image: 'gpt-image-2', video: 'veo-3.1' },
            preset_motions: [],
            platform_constraints_version: 'v1',
            api_version: 'v1',
            degraded_services: [],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        )
      }
      return new Response('{}', {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }
    globalThis.fetch = vi.fn(fetchStub)
  })

  afterEach(() => {
    globalThis.fetch = originalFetch
  })

  it('renders the dashboard with empty state when authenticated and no characters exist', async () => {
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
    expect(screen.getByRole('heading', { name: '我的角色' })).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getByTestId('dashboard-empty')).toBeInTheDocument()
    })
  })
})
