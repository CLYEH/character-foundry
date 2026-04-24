import { beforeEach, describe, expect, it } from 'vitest'

import { AUTH_STORAGE_KEY, useAuthStore, type AuthUser } from './authStore'

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
    user: null,
    expiresAt: null,
  })
  localStorage.removeItem(AUTH_STORAGE_KEY)
}

describe('authStore', () => {
  beforeEach(resetStore)

  it('login() sets tokens, user, and expiresAt', () => {
    const before = Date.now()
    useAuthStore.getState().login({
      accessToken: 'a1',
      refreshToken: 'r1',
      user: sampleUser,
      expiresIn: 900,
    })
    const s = useAuthStore.getState()
    expect(s.accessToken).toBe('a1')
    expect(s.refreshToken).toBe('r1')
    expect(s.user).toEqual(sampleUser)
    expect(s.expiresAt).not.toBeNull()
    expect(s.expiresAt!).toBeGreaterThanOrEqual(before + 900 * 1000 - 50)
  })

  it('persists state to localStorage under the cf-auth key', () => {
    useAuthStore.getState().login({
      accessToken: 'a1',
      refreshToken: 'r1',
      user: sampleUser,
      expiresIn: 900,
    })
    const raw = localStorage.getItem(AUTH_STORAGE_KEY)
    expect(raw).not.toBeNull()
    const persisted = JSON.parse(raw!) as { state: { accessToken: string; refreshToken: string } }
    expect(persisted.state.accessToken).toBe('a1')
    expect(persisted.state.refreshToken).toBe('r1')
  })

  it('logout() clears every auth field', () => {
    useAuthStore.getState().login({
      accessToken: 'a1',
      refreshToken: 'r1',
      user: sampleUser,
      expiresIn: 900,
    })
    useAuthStore.getState().logout()
    const s = useAuthStore.getState()
    expect(s.accessToken).toBeNull()
    expect(s.refreshToken).toBeNull()
    expect(s.user).toBeNull()
    expect(s.expiresAt).toBeNull()
  })

  it('updateAccessToken() rotates only the access token', () => {
    useAuthStore.getState().login({
      accessToken: 'a1',
      refreshToken: 'r1',
      user: sampleUser,
      expiresIn: 900,
    })
    useAuthStore.getState().updateAccessToken('a2', 60)
    const s = useAuthStore.getState()
    expect(s.accessToken).toBe('a2')
    expect(s.refreshToken).toBe('r1')
    expect(s.user).toEqual(sampleUser)
  })
})
