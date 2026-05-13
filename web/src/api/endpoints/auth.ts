import { apiFetch } from '@/api/client'
import type { AuthUser } from '@/stores/authStore'

export interface MeResponse {
  user: AuthUser
}

export function getMe() {
  return apiFetch<MeResponse>('/v1/auth/me')
}
