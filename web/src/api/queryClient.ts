import { createElement } from 'react'
import { QueryCache, QueryClient } from '@tanstack/react-query'

import { ApiError } from './client'
import { AgentError, mapAgentErrorToUI } from '@/lib/agentError'
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
function handleQueryError(err: unknown) {
  const agentError = AgentError.from(err)
  const layer = mapAgentErrorToUI(agentError)
  if (layer !== 'toast') return

  toast.agentError(agentError, {
    description: createElement(ErrorToast, { error: agentError }),
  })
}

export const queryClient = new QueryClient({
  queryCache: new QueryCache({
    onError: (err, query) => {
      // Respect per-query `meta.suppressGlobalError` so forms can handle
      // AUTH_INVALID_CREDENTIALS / VALIDATION_* without a duplicate toast.
      if (query.meta?.suppressGlobalError) return
      handleQueryError(err)
    },
  }),
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      gcTime: 5 * 60_000,
      retry: (failureCount, error) => {
        if (error instanceof AgentError) {
          return error.retryable && failureCount < 2
        }
        if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
          return false
        }
        return failureCount < 2
      },
      refetchOnWindowFocus: false,
    },
    mutations: {
      retry: 0,
    },
  },
})
