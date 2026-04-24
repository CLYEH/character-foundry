import { create } from 'zustand'
import { persist } from 'zustand/middleware'

export interface AuthUser {
  id: string
  name: string
  email: string
  team_id: string
  created_at: string
}

interface LoginArgs {
  accessToken: string
  refreshToken: string
  user: AuthUser
  expiresIn: number
}

interface AuthState {
  accessToken: string | null
  refreshToken: string | null
  user: AuthUser | null
  expiresAt: number | null
  login: (args: LoginArgs) => void
  logout: () => void
  updateAccessToken: (accessToken: string, expiresIn: number) => void
}

export const AUTH_STORAGE_KEY = 'cf-auth'

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      accessToken: null,
      refreshToken: null,
      user: null,
      expiresAt: null,
      login: ({ accessToken, refreshToken, user, expiresIn }) =>
        set({
          accessToken,
          refreshToken,
          user,
          expiresAt: Date.now() + expiresIn * 1000,
        }),
      logout: () => set({ accessToken: null, refreshToken: null, user: null, expiresAt: null }),
      updateAccessToken: (accessToken, expiresIn) =>
        set({ accessToken, expiresAt: Date.now() + expiresIn * 1000 }),
    }),
    {
      name: AUTH_STORAGE_KEY,
      partialize: (s) => ({
        accessToken: s.accessToken,
        refreshToken: s.refreshToken,
        user: s.user,
        expiresAt: s.expiresAt,
      }),
    },
  ),
)
