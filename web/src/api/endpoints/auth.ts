import { apiFetch } from '@/api/client'
import type { AuthUser } from '@/stores/authStore'

export interface LoginRequest {
  email: string
  password: string
}

export interface LoginResponse {
  access_token: string
  refresh_token: string
  expires_in: number
  user: AuthUser
}

export interface RefreshResponse {
  access_token: string
  expires_in: number
}

export interface MeResponse {
  user: AuthUser
}

export interface LogoutResponse {
  ok: boolean
}

export function login(input: LoginRequest) {
  return apiFetch<LoginResponse>('/v1/auth/login', {
    method: 'POST',
    body: JSON.stringify(input),
    skipAuth: true,
  })
}

export function refresh(refreshToken: string) {
  return apiFetch<RefreshResponse>('/v1/auth/refresh', {
    method: 'POST',
    body: JSON.stringify({ refresh_token: refreshToken }),
    skipAuth: true,
  })
}

export function logout(refreshToken: string) {
  return apiFetch<LogoutResponse>('/v1/auth/logout', {
    method: 'POST',
    body: JSON.stringify({ refresh_token: refreshToken }),
  })
}

export function getMe() {
  return apiFetch<MeResponse>('/v1/auth/me')
}
