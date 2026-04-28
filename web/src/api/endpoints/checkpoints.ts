import { apiFetch } from '@/api/client'
import type { CreationSession } from '@/api/endpoints/characters'
import type { MenuSelections } from '@/constants/menu_options'

export interface Checkpoint {
  id: string
  creation_session_id: string
  sequence: number
  prompt_summary: string
  output_image_url: string | null
  thumbnail_url: string | null
  selected_as_base: boolean
  created_at: string
}

export interface CreationSessionDetail {
  session: CreationSession
  checkpoints: Checkpoint[]
}

export type CheckpointMode = 'retry_same' | 'remix' | 'fresh'

export interface CreateCheckpointRequest {
  mode: CheckpointMode
  base_checkpoint_id: string | null
  menu_selections: MenuSelections | null
  freeform_note: string | null
  reference_image_ids: string[] | null
}

export interface CreateCheckpointResponse {
  task_id: string
  checkpoint_id: string
}

export function getCreationSession(sessionId: string): Promise<CreationSessionDetail> {
  return apiFetch<CreationSessionDetail>(`/v1/creation-sessions/${sessionId}`)
}

export function createCheckpoint(
  sessionId: string,
  body: CreateCheckpointRequest,
): Promise<CreateCheckpointResponse> {
  return apiFetch<CreateCheckpointResponse>(`/v1/creation-sessions/${sessionId}/checkpoints`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}
