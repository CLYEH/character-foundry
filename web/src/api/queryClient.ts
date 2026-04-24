import { createElement } from 'react'
import { MutationCache, QueryCache, QueryClient } from '@tanstack/react-query'

import { ApiError } from './client'
import { AgentError, hasAgentErrorBody, mapAgentErrorToUI } from '@/lib/agentError'
import { toast } from '@/stores/toastStore'
import { ErrorToast } from '@/components/composite/ErrorToast'

/**
 * Default onError for TanStack Query. Feature code can still override this
 * per-query/mutation (e.g. to map form validation errors inline); this handler
 * only fires when a caller hasn't supplied its own. We route to the correct
 * Layer-2/3 UI via `mapAgentErrorToUI`:
 *   - `toast`  → Sonner toast with the AgentError detail body
 *   - `inline` → leave it to the form owner (no global toast)
 *   - `page`   → leave it to routing / ErrorBoundary
 */
function handleGlobalError(err: unknown, suppressed: boolean) {
  if (suppressed) return
  const agentError = AgentError.from(err)
  const layer = mapAgentErrorToUI(agentError)
  if (layer !== 'toast') return

  toast.agentError(agentError, {
    description: createElement(ErrorToast, { error: agentError }),
  })
}

function shouldRetry(failureCount: number, error: unknown): boolean {
  const agentError = AgentError.from(error)
  // If the backend gave us a structured AgentError, its `retryable` flag wins.
  if (hasAgentErrorBody(error)) {
    return agentError.retryable && failureCount < 2
  }
  // No structured body → fall back to HTTP-status heuristic (don't retry 4xx).
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
    return false
  }
  return failureCount < 2
}

export const queryClient = new QueryClient({
  queryCache: new QueryCache({
    onError: (err, query) => {
      // Respect per-query `meta.suppressGlobalError` so forms can handle
      // AUTH_INVALID_CREDENTIALS / VALIDATION_* without a duplicate toast.
      handleGlobalError(err, Boolean(query.meta?.suppressGlobalError))
    },
  }),
  mutationCache: new MutationCache({
    // TanStack Query v5: signature is (error, variables, onMutateResult, mutation, context).
    onError: (err, _vars, _onMutateResult, mutation) => {
      // Same opt-out for write-path errors: a mutation that handles its own
      // validation (e.g. login form) sets `meta: { suppressGlobalError: true }`.
      handleGlobalError(err, Boolean(mutation.meta?.suppressGlobalError))
    },
  }),
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      gcTime: 5 * 60_000,
      retry: shouldRetry,
      refetchOnWindowFocus: false,
    },
    mutations: {
      retry: 0,
    },
  },
})
