import { ApiError } from '@/api/client'

/**
 * Raw AgentError shape as emitted by the backend (see
 * `planning/backend/api-shape.md` §4). Fields are snake_case; this module
 * converts them to camelCase on the `AgentError` instance.
 */
export interface AgentErrorPayload {
  code?: string
  message?: string
  problem?: string
  cause?: string
  fix?: string
  docs_url?: string
  retryable?: boolean
  request_id?: string
}

export type AgentErrorUILayer = 'inline' | 'toast' | 'page'

export class AgentError extends Error {
  code: string
  problem: string
  cause: string
  fix: string
  docsUrl?: string
  retryable: boolean
  requestId: string

  constructor(raw: AgentErrorPayload) {
    super(raw.message ?? 'Unknown error')
    this.name = 'AgentError'
    this.code = raw.code ?? 'INTERNAL_UNEXPECTED_ERROR'
    this.problem = raw.problem ?? ''
    this.cause = raw.cause ?? ''
    this.fix = raw.fix ?? ''
    this.docsUrl = raw.docs_url
    this.retryable = raw.retryable ?? false
    this.requestId = raw.request_id ?? ''
  }

  isCategory(prefix: string): boolean {
    return this.code.startsWith(prefix)
  }

  /**
   * Best-effort coercion of any thrown value into an AgentError so downstream
   * handlers (toast / boundary / form) can work with a single shape. ApiError
   * bodies carrying `{ error: {...} }` are unwrapped; everything else becomes
   * an `INTERNAL_UNEXPECTED_ERROR`.
   */
  static from(err: unknown): AgentError {
    if (err instanceof AgentError) return err

    if (err instanceof ApiError) {
      const payload = extractAgentErrorPayload(err.body)
      if (payload) return new AgentError(payload)
      return new AgentError({ code: err.code, message: err.message })
    }

    if (err instanceof Error) {
      return new AgentError({ code: 'INTERNAL_UNEXPECTED_ERROR', message: err.message })
    }

    return new AgentError({ code: 'INTERNAL_UNEXPECTED_ERROR', message: String(err) })
  }
}

function extractAgentErrorPayload(body: unknown): AgentErrorPayload | null {
  if (!body || typeof body !== 'object') return null
  if ('error' in body) {
    const inner = (body as { error: unknown }).error
    if (inner && typeof inner === 'object') return inner as AgentErrorPayload
  }
  return null
}

/**
 * Decide which UI layer (inline / toast / page) should render a given error.
 * Rules come from T-011 notes in the ticket — keep them in sync.
 */
export function mapAgentErrorToUI(err: AgentError): AgentErrorUILayer {
  if (err.isCategory('VALIDATION_') || err.isCategory('CONFLICT_')) return 'inline'
  if (err.code === 'AUTH_EXPIRED') return 'page'
  if (err.code === 'AUTH_INVALID_CREDENTIALS') return 'inline'
  if (err.isCategory('NOT_FOUND_')) return 'page'
  if (
    err.isCategory('MODEL_') ||
    err.isCategory('PROMPT_') ||
    err.isCategory('STORAGE_') ||
    err.isCategory('QUOTA_')
  ) {
    return 'toast'
  }
  return 'toast'
}
