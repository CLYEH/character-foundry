import type { ReactNode } from 'react'

import { ErrorPage } from './ErrorPage'

export interface GenericErrorPageProps {
  title?: string
  description?: ReactNode
  onRetry?: () => void
  retryLabel?: string
}

export function GenericErrorPage({
  title = '出了點狀況',
  description = '系統發生非預期錯誤，請稍後再試。',
  onRetry,
  retryLabel = '重試',
}: GenericErrorPageProps) {
  return (
    <ErrorPage
      icon="💥"
      title={title}
      description={description}
      primaryAction={onRetry ? { label: retryLabel, onClick: onRetry } : undefined}
      testId="generic-error-page"
    />
  )
}
