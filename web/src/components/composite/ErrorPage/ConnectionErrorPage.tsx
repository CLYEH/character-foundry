import { ErrorPage } from './ErrorPage'

export interface ConnectionErrorPageProps {
  onRetry?: () => void
}

export function ConnectionErrorPage({ onRetry }: ConnectionErrorPageProps) {
  const retry =
    onRetry ??
    (() => {
      if (typeof window !== 'undefined') window.location.reload()
    })

  return (
    <ErrorPage
      icon="⚠"
      title="連線不到伺服器"
      description="請檢查網路連線後再試。"
      primaryAction={{ label: '重新整理', onClick: retry }}
      testId="connection-error-page"
    />
  )
}
