import { describe, expect, it } from 'vitest'

import { ApiError } from '@/api/client'
import { AgentError, mapAgentErrorToUI } from './agentError'

describe('AgentError', () => {
  it('maps snake_case backend fields to camelCase properties', () => {
    const err = new AgentError({
      code: 'MODEL_TIMEOUT',
      message: '模型逾時',
      problem: 'gpt-image-2 took longer than 60s',
      cause: 'Upstream provider slow',
      fix: 'Retry in a few seconds',
      docs_url: 'https://docs.internal/errors/MODEL_TIMEOUT',
      retryable: true,
      request_id: 'req_abc',
    })

    expect(err.code).toBe('MODEL_TIMEOUT')
    expect(err.message).toBe('模型逾時')
    expect(err.problem).toBe('gpt-image-2 took longer than 60s')
    expect(err.cause).toBe('Upstream provider slow')
    expect(err.fix).toBe('Retry in a few seconds')
    expect(err.docsUrl).toBe('https://docs.internal/errors/MODEL_TIMEOUT')
    expect(err.retryable).toBe(true)
    expect(err.requestId).toBe('req_abc')
  })

  it('falls back to INTERNAL_UNEXPECTED_ERROR when no code is provided', () => {
    const err = new AgentError({ message: 'oops' })
    expect(err.code).toBe('INTERNAL_UNEXPECTED_ERROR')
    expect(err.retryable).toBe(false)
  })

  describe('isCategory', () => {
    it('returns true for matching prefixes', () => {
      const err = new AgentError({ code: 'VALIDATION_NAME_TOO_LONG' })
      expect(err.isCategory('VALIDATION_')).toBe(true)
    })

    it('returns false for non-matching prefixes', () => {
      const err = new AgentError({ code: 'VALIDATION_NAME_TOO_LONG' })
      expect(err.isCategory('MODEL_')).toBe(false)
    })
  })

  describe('AgentError.from', () => {
    it('unwraps an ApiError carrying an AgentError-shaped body', () => {
      const apiErr = new ApiError(400, 'PROMPT_CONFLICT', 'Prompt 衝突', {
        error: {
          code: 'PROMPT_CONFLICT',
          message: 'Prompt 衝突',
          problem: 'note vs constraint',
          cause: 'user wrote "cluttered market" with transparent-bg constraint',
          fix: 'remove background keywords',
          retryable: false,
          request_id: 'req_1',
        },
      })

      const err = AgentError.from(apiErr)
      expect(err).toBeInstanceOf(AgentError)
      expect(err.code).toBe('PROMPT_CONFLICT')
      expect(err.problem).toBe('note vs constraint')
      expect(err.requestId).toBe('req_1')
    })

    it('synthesises from an ApiError without an AgentError body', () => {
      const apiErr = new ApiError(404, 'HTTP_404', 'Not Found', null)
      const err = AgentError.from(apiErr)
      expect(err.code).toBe('HTTP_404')
      expect(err.message).toBe('Not Found')
    })

    it('wraps a plain Error as INTERNAL_UNEXPECTED_ERROR', () => {
      const err = AgentError.from(new Error('boom'))
      expect(err.code).toBe('INTERNAL_UNEXPECTED_ERROR')
      expect(err.message).toBe('boom')
    })

    it('returns the same instance when given an AgentError', () => {
      const original = new AgentError({ code: 'MODEL_TIMEOUT', message: 'timeout' })
      expect(AgentError.from(original)).toBe(original)
    })

    it('coerces non-Error thrown values', () => {
      const err = AgentError.from('string literal')
      expect(err.code).toBe('INTERNAL_UNEXPECTED_ERROR')
      expect(err.message).toBe('string literal')
    })
  })
})

describe('mapAgentErrorToUI', () => {
  it.each([
    ['VALIDATION_NAME_TOO_LONG', 'inline'],
    ['CONFLICT_DUPLICATE_NAME', 'inline'],
    ['AUTH_INVALID_CREDENTIALS', 'inline'],
    ['AUTH_EXPIRED', 'page'],
    ['NOT_FOUND_CHARACTER', 'page'],
    ['MODEL_TIMEOUT', 'toast'],
    ['PROMPT_CONFLICT', 'toast'],
    ['STORAGE_WRITE_FAILED', 'toast'],
    ['QUOTA_EXCEEDED', 'toast'],
    ['INTERNAL_UNEXPECTED_ERROR', 'toast'],
    ['SOMETHING_UNKNOWN', 'toast'],
  ])('maps %s to %s', (code, expected) => {
    expect(mapAgentErrorToUI(new AgentError({ code }))).toBe(expected)
  })
})
