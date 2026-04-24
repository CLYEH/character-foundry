import { create } from 'zustand'

export interface AuthUser {
  id: string
  email: string
  display_name?: string
}

interface AuthState {
  accessToken: string | null
  refreshToken: string | null
  user: AuthUser | null
}

export const useAuthStore = create<AuthState>()(() => ({
  accessToken: null,
  refreshToken: null,
  user: null,
}))
