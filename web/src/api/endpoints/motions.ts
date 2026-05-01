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

export interface CreateMotionRequest {
  motion_type: MotionType
  name: string
  description?: string | null
}

export interface CreateMotionResponse {
  task_id: string
  motion_id: string
}

export function createMotionForBase(
  baseId: string,
  body: CreateMotionRequest,
): Promise<CreateMotionResponse> {
  return apiFetch<CreateMotionResponse>(`/v1/bases/${baseId}/motions`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function createMotionForAlias(
  aliasId: string,
  body: CreateMotionRequest,
): Promise<CreateMotionResponse> {
  return apiFetch<CreateMotionResponse>(`/v1/aliases/${aliasId}/motions`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function createMotion(
  parent: MotionParentRef,
  body: CreateMotionRequest,
): Promise<CreateMotionResponse> {
  return parent.type === 'base'
    ? createMotionForBase(parent.id, body)
    : createMotionForAlias(parent.id, body)
}

export function deleteMotion(motionId: string): Promise<void> {
  return apiFetch<void>(`/v1/motions/${motionId}`, { method: 'DELETE' })
}
