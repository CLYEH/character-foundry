import type { ReactNode } from 'react'
import { ErrorBoundary as ReactErrorBoundary, type FallbackProps } from 'react-error-boundary'

import {
  ConnectionErrorPage,
  GenericErrorPage,
  NotFoundPage,
} from '@/components/composite/ErrorPage'
import { AgentError } from '@/lib/agentError'

export interface ErrorBoundaryProps {
  children: ReactNode
  /** Custom fallback — overrides the default AgentError-aware Layer-3 page. */
  fallback?: (props: FallbackProps) => ReactNode
  /** Fired when the boundary catches an error (for logging / Sentry hook). */
  onError?: (error: unknown, info: { componentStack?: string | null }) => void
}

/**
 * Layer-3 catch-all. Converts thrown values into an `AgentError` and renders
 * the matching Layer-3 page (404 / connection / generic). Form validation and
 * async-task failures are expected to be caught before they reach here.
 */
export function ErrorBoundary({ children, fallback, onError }: ErrorBoundaryProps) {
  return (
    <ReactErrorBoundary
      FallbackComponent={fallback ? fallbackAdapter(fallback) : DefaultErrorFallback}
      onError={(error, info) => {
        onError?.(error, { componentStack: info.componentStack })
      }}
    >
      {children}
    </ReactErrorBoundary>
  )
}

function fallbackAdapter(fallback: (props: FallbackProps) => ReactNode) {
  return function Fallback(props: FallbackProps) {
    return <>{fallback(props)}</>
  }
}

function DefaultErrorFallback({ error, resetErrorBoundary }: FallbackProps) {
  const agentError = AgentError.from(error)

  if (agentError.isCategory('NOT_FOUND_')) {
    return <NotFoundPage />
  }

  if (isLikelyNetworkError(error, agentError)) {
    return <ConnectionErrorPage onRetry={resetErrorBoundary} />
  }

  return (
    <GenericErrorPage
      description={agentError.message || '系統發生非預期錯誤，請稍後再試。'}
      onRetry={resetErrorBoundary}
    />
  )
}

function isLikelyNetworkError(raw: unknown, agentError: AgentError): boolean {
  if (agentError.code === 'NETWORK_UNREACHABLE') return true
  if (raw instanceof TypeError && /fetch|network/i.test(raw.message)) return true
  return false
}
