import type { ReactNode } from 'react'
import { Link } from 'react-router'

import { Button } from '@/components/ui/button'

export interface ErrorPageProps {
  icon: ReactNode
  title: string
  description?: ReactNode
  /** Primary action button (e.g. retry). Rendered first. */
  primaryAction?: { label: string; onClick: () => void }
  /** Secondary action (defaults to "回首頁" home link). Pass `null` to suppress. */
  secondaryAction?: { label: string; to: string } | null
  /** Forwarded to the outer <section> for tests. */
  testId?: string
}

const DEFAULT_HOME_ACTION = { label: '回首頁', to: '/' } as const

export function ErrorPage({
  icon,
  title,
  description,
  primaryAction,
  secondaryAction,
  testId,
}: ErrorPageProps) {
  const secondary = secondaryAction === undefined ? DEFAULT_HOME_ACTION : secondaryAction

  return (
    <section
      className="flex min-h-[60vh] flex-col items-center justify-center gap-4 px-4 text-center"
      data-testid={testId}
    >
      <div className="text-5xl" aria-hidden>
        {icon}
      </div>
      <h1 className="text-2xl font-semibold">{title}</h1>
      {description != null && (
        <p className="max-w-md text-sm text-muted-foreground">{description}</p>
      )}
      {(primaryAction || secondary) && (
        <div className="mt-2 flex flex-wrap items-center justify-center gap-3">
          {primaryAction && (
            <Button type="button" onClick={primaryAction.onClick}>
              {primaryAction.label}
            </Button>
          )}
          {secondary && (
            <Button asChild variant="outline">
              <Link to={secondary.to}>{secondary.label}</Link>
            </Button>
          )}
        </div>
      )}
    </section>
  )
}
