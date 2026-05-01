import { apiFetch } from '@/api/client'

/**
 * Motion DTOs (api-shape §5.4 / §6.5).
 *
 * The 5 preset slots on `MotionRow` rely on `motion_type` being one of
 * `PRESET_MOTION_TYPES`; the server enumerates the same set in
 * `app.schemas.prompt.MotionType`.
 */
export type MotionType =
  | 'preset_wave'
  | 'preset_nod'
  | 'preset_gesture'
  | 'preset_happy'
  | 'preset_idle'
  | 'custom'

export type MotionParentType = 'base' | 'alias'

export const PRESET_MOTION_TYPES = [
  'preset_wave',
  'preset_nod',
  'preset_gesture',
  'preset_happy',
  'preset_idle',
] as const satisfies readonly Exclude<MotionType, 'custom'>[]

export type PresetMotionType = (typeof PRESET_MOTION_TYPES)[number]

export const PRESET_LABELS: Record<PresetMotionType, string> = {
  preset_wave: '招手',
  preset_nod: '點頭',
  preset_gesture: '手勢',
  preset_happy: '開心',
  preset_idle: '待機',
}

export interface MotionParentRef {
  type: MotionParentType
  id: string
}

export interface Motion {
  id: string
  parent: MotionParentRef
  motion_type: MotionType
  name: string
  description: string | null
  video_url: string | null
  thumbnail_url: string | null
  duration_ms: number | null
  created_at: string
}

export interface MotionListResponse {
  items: Motion[]
}

export function listAliasMotions(aliasId: string): Promise<MotionListResponse> {
  return apiFetch<MotionListResponse>(`/v1/aliases/${aliasId}/motions`)
}

export function listBaseMotions(baseId: string): Promise<MotionListResponse> {
  return apiFetch<MotionListResponse>(`/v1/bases/${baseId}/motions`)
}
