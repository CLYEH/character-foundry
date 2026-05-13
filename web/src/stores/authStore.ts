import { create } from 'zustand'
import { persist } from 'zustand/middleware'

import { apiBaseUrl } from '@/config'
import { refreshOauthToken, revokeOauthToken } from '@/lib/oauth-client'

export interface AuthUser {
  id: string
  name: string
  email: string
  team_id: string
  created_at: string
}

export type TokenSource = 'jwt' | 'oauth'

interface SetAuthArgs {
  accessToken: string
  refreshToken: string
  user: AuthUser
  expiresIn: number
  tokenSource: TokenSource
  idToken?: string | null
}

interface AuthState {
  accessToken: string | null
  refreshToken: string | null
  idToken: string | null
  user: AuthUser | null
  expiresAt: number | null
  tokenSource: TokenSource | null
  setAuth: (args: SetAuthArgs) => void
  logout: () => void
  /** Local-only access-token rotation; used by the legacy JWT refresh path. */
  updateAccessToken: (accessToken: string, expiresIn: number) => void
  /**
   * Server-side refresh. Picks the JWT or OAuth endpoint based on
   * `tokenSource`. Returns true when `accessToken` (and possibly
   * `refreshToken`) was rotated. Returns false if there is no refresh token,
   * the session changed mid-flight, or the upstream call failed.
   */
  refresh: () => Promise<boolean>
}

export const AUTH_STORAGE_KEY = 'cf-auth'

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      accessToken: null,
      refreshToken: null,
      idToken: null,
      user: null,
      expiresAt: null,
      tokenSource: null,
      setAuth: ({ accessToken, refreshToken, user, expiresIn, tokenSource, idToken = null }) =>
        set({
          accessToken,
          refreshToken,
          idToken,
          user,
          tokenSource,
          expiresAt: Date.now() + expiresIn * 1000,
        }),
      logout: () =>
        set({
          accessToken: null,
          refreshToken: null,
          idToken: null,
          user: null,
          expiresAt: null,
          tokenSource: null,
        }),
      updateAccessToken: (accessToken, expiresIn) =>
        set({ accessToken, expiresAt: Date.now() + expiresIn * 1000 }),
      refresh: async () => {
        // Callers SHOULD route through `attemptTokenRefresh()` in
        // `api/client.ts` for single-flight; calling `refresh()` directly on
        // parallel 401s will fire one request per call.
        const startToken = get().refreshToken
        const source = get().tokenSource
        if (!startToken) return false
        try {
          if (source === 'oauth') {
            const data = await refreshOauthToken(startToken)
            // Drop the result if the session rotated mid-flight (logout, or
            // a fresh login bumped the refresh token) — same guard as the
            // legacy JWT path.
            if (get().refreshToken !== startToken) return false
            set({
              accessToken: data.access_token,
              refreshToken: data.refresh_token ?? startToken,
              expiresAt: Date.now() + data.expires_in * 1000,
            })
            return true
          }
          const res = await fetch(`${apiBaseUrl}/v1/auth/refresh`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ refresh_token: startToken }),
          })
          if (!res.ok) return false
          const data = (await res.json()) as { access_token: string; expires_in: number }
          if (get().refreshToken !== startToken) return false
          set({
            accessToken: data.access_token,
            expiresAt: Date.now() + data.expires_in * 1000,
          })
          return true
        } catch {
          return false
        }
      },
    }),
    {
      name: AUTH_STORAGE_KEY,
      partialize: (s) => ({
        accessToken: s.accessToken,
        refreshToken: s.refreshToken,
        user: s.user,
        expiresAt: s.expiresAt,
        tokenSource: s.tokenSource,
        // `idToken` is intentionally NOT persisted: it carries IdP-issued
        // PII (email, sub, at_hash) and nothing currently reads it across
        // page loads. Lives in volatile state only until end-session or an
        // account-info UI starts consuming it.
      }),
    },
  ),
)

/**
 * Best-effort server-side logout. For OAuth sessions, posts to Authentik's
 * revoke endpoint with the refresh token. For JWT sessions, posts to the
 * legacy `/v1/auth/logout`. Always resolves — local state is cleared by the
 * caller regardless of the network outcome.
 */
export async function signOutServer(): Promise<void> {
  const { tokenSource, refreshToken, accessToken } = useAuthStore.getState()
  if (!refreshToken) return
  if (tokenSource === 'oauth') {
    await revokeOauthToken(refreshToken)
    return
  }
  try {
    await fetch(`${apiBaseUrl}/v1/auth/logout`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${accessToken ?? ''}`,
      },
      body: JSON.stringify({ refresh_token: refreshToken }),
    })
  } catch {
    /* best-effort */
  }
}
