import { toast as sonnerToast } from 'sonner'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from './client'
import { queryClient } from './queryClient'
import { AgentError } from '@/lib/agentError'

// Module-singleton queryClient — do NOT resetModules here. Doing so creates
// separate `ApiError` constructor identities for the test vs. queryClient
// modules and breaks the `instanceof ApiError` checks inside retry / onError.

describe('queryClient', () => {
  let errorSpy: ReturnType<typeof vi.fn>

  beforeEach(() => {
    errorSpy = vi.spyOn(sonnerToast, 'error').mockImplementation(() => 'id' as never) as never
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  describe('global error pipeline', () => {
    it('toasts on query failure', () => {
      queryClient
        .getQueryCache()
        .config.onError?.(new ApiError(500, 'MODEL_TIMEOUT', 'timeout', null), {
          meta: undefined,
        } as never)

      expect(errorSpy).toHaveBeenCalledTimes(1)
    })

    it('toasts on mutation failure (regression: T-011 Codex P2)', () => {
      const cache = queryClient.getMutationCache()
      expect(cache.config.onError).toBeDefined()

      cache.config.onError?.(
        new ApiError(500, 'MODEL_TIMEOUT', 'timeout', null),
        undefined,
        undefined,
        { meta: undefined } as never,
        undefined as never,
      )

      expect(errorSpy).toHaveBeenCalledTimes(1)
    })

    it('honors meta.suppressGlobalError on mutations', () => {
      queryClient.getMutationCache().config.onError?.(
        new ApiError(401, 'AUTH_INVALID_CREDENTIALS', 'bad creds', {
          error: { code: 'AUTH_INVALID_CREDENTIALS', message: 'bad creds' },
        }),
        undefined,
        undefined,
        { meta: { suppressGlobalError: true } } as never,
        undefined as never,
      )

      expect(errorSpy).not.toHaveBeenCalled()
    })

    it('does not toast for VALIDATION_* (inline layer)', () => {
      queryClient.getQueryCache().config.onError?.(
        new ApiError(400, 'VALIDATION_NAME_TOO_LONG', 'too long', {
          error: { code: 'VALIDATION_NAME_TOO_LONG', message: 'too long' },
        }),
        { meta: undefined } as never,
      )

      expect(errorSpy).not.toHaveBeenCalled()
    })
  })

  describe('retry predicate (regression: T-011 Codex P2)', () => {
    const retry = queryClient.getDefaultOptions().queries?.retry as (
      n: number,
      err: unknown,
    ) => boolean

    it('honors server-side retryable=true on ApiError carrying an AgentError body', () => {
      const retryable = new ApiError(502, 'MODEL_TIMEOUT', 'timeout', {
        error: { code: 'MODEL_TIMEOUT', message: 'timeout', retryable: true },
      })

      expect(retry(0, retryable)).toBe(true)
      expect(retry(1, retryable)).toBe(true)
      expect(retry(2, retryable)).toBe(false)
    })

    it('honors server-side retryable=false even for 5xx', () => {
      const notRetryable = new ApiError(500, 'INTERNAL_UNEXPECTED_ERROR', 'boom', {
        error: {
          code: 'INTERNAL_UNEXPECTED_ERROR',
          message: 'boom',
          retryable: false,
        },
      })

      expect(retry(0, notRetryable)).toBe(false)
    })

    it('falls back to HTTP-status heuristic when there is no AgentError body', () => {
      const bareNotFound = new ApiError(404, 'HTTP_404', 'Not Found', null)
      expect(retry(0, bareNotFound)).toBe(false)

      const bareServerError = new ApiError(503, 'HTTP_503', 'Unavailable', null)
      expect(retry(0, bareServerError)).toBe(true)
      expect(retry(1, bareServerError)).toBe(true)
      expect(retry(2, bareServerError)).toBe(false)
    })

    it('treats a thrown AgentError directly as the source of truth', () => {
      expect(retry(0, new AgentError({ code: 'MODEL_TIMEOUT', retryable: true }))).toBe(true)
      expect(retry(0, new AgentError({ code: 'VALIDATION_X', retryable: false }))).toBe(false)
    })
  })
})
