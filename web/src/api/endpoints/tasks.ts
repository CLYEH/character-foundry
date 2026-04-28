import { apiFetch } from '@/api/client'
import type { AgentErrorPayload } from '@/lib/agentError'
import type { Checkpoint } from '@/api/endpoints/checkpoints'

export type TaskStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'

export type TaskType =
  | 'create_checkpoint'
  | 'create_alias'
  | 'create_motion'
  | 'export_zip'
  | 'copy_character'

export interface Task<TResult = unknown> {
  id: string
  status: TaskStatus
  task_type: TaskType
  entity_type: string
  entity_id: string
  queue_position: number | null
  progress: number | null
  estimated_duration_ms: number | null
  cancel_requested: boolean
  cancel_requested_at: string | null
  started_at: string | null
  completed_at: string | null
  result: TResult | null
  error: AgentErrorPayload | null
  created_at: string
}

export type CancelOutcome =
  | 'cancelled_immediately'
  | 'cancel_pending'
  | 'too_late_completed'
  | 'too_late_failed'

export interface CancelTaskResponse {
  task: Task
  cancel_outcome: CancelOutcome
}

/**
 * SSE event payload streamed by `/v1/tasks/{id}/stream`. Event type names are
 * declared by the backend; we match them in `useTaskStream` regardless of the
 * shape inside `data`. Only `data` (a JSON-encoded TaskEvent) is contractual.
 */
export interface TaskEvent {
  status: TaskStatus
  queue_position?: number | null
  progress?: number | null
  partial_preview_url?: string | null
  message?: string | null
  result?: { checkpoint?: Checkpoint } | null
  error?: AgentErrorPayload | null
}

export function getTask(taskId: string): Promise<Task> {
  return apiFetch<Task>(`/v1/tasks/${taskId}`)
}

export function cancelTask(taskId: string): Promise<CancelTaskResponse> {
  return apiFetch<CancelTaskResponse>(`/v1/tasks/${taskId}/cancel`, {
    method: 'POST',
  })
}
