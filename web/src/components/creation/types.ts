import type { Checkpoint, CreateCheckpointRequest } from '@/api/endpoints/checkpoints'
import type { Task, TaskEvent } from '@/api/endpoints/tasks'
import type { AgentErrorPayload } from '@/lib/agentError'

export type CardStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'

export interface CheckpointCardModel {
  /** UUID assigned by backend at submit-time; stable across placeholder → final. */
  checkpointId: string
  /** Backend sequence number; null until the worker writes the checkpoint row. */
  sequence: number | null
  status: CardStatus
  /** Latest event from the SSE stream — null for already-completed checkpoints loaded from GET. */
  event: TaskEvent | null
  /** Final checkpoint DTO once available (either from initial GET or SSE result). */
  checkpoint: Checkpoint | null
  /**
   * Error payload for failed cards. Lifted onto the model (rather than read
   * from `event.error`) so synthetic terminal events from the cancel mutation
   * (`too_late_failed`) — which never enter the SSE `events` map — still
   * surface the error message.
   */
  error: AgentErrorPayload | null
  /** Inputs that produced this card; required to support [重試] / [用這張再改] prefill. */
  request: CreateCheckpointRequest | null
  /** task_id for the in-flight stream; null after terminal status. */
  taskId: string | null
  /** Reflects `Task.cancel_requested` once the user has hit cancel. */
  cancelRequested: boolean
}

export function statusFromEvent(event: TaskEvent | null): CardStatus | null {
  return event ? event.status : null
}

export function pickError(model: CheckpointCardModel): AgentErrorPayload | null {
  return model.event?.error ?? null
}

export type RemixContext = {
  baseCheckpointId: string
  baseSequence: number | null
} | null

export type CancelOutcomeFromTask = Pick<Task, 'cancel_requested' | 'status'>
