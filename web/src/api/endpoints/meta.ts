import { apiFetch } from '@/api/client'

export interface PresetMotion {
  type: string
  display_name_zh: string
  display_name_en: string
  default_duration_ms: number
}

export interface DegradedService {
  service: string
  reason?: string
  retry_at?: string
  message?: string
}

export interface MetaResponse {
  models: Record<string, string>
  preset_motions: PresetMotion[]
  platform_constraints_version: string
  api_version: string
  degraded_services: DegradedService[]
}

export function getMeta() {
  // /v1/meta is public. Skip auth so a stale / missing token doesn't detour
  // through the 401 → refresh flow just to fetch metadata that feeds the
  // DegradedBanner — in an infra incident the banner is what the user needs
  // to see, and we shouldn't block it on auth plumbing.
  return apiFetch<MetaResponse>('/v1/meta', { skipAuth: true })
}
