import { useState } from 'react'
import { ChevronDown, ChevronUp } from 'lucide-react'

import type { AgentError } from '@/lib/agentError'
import { cn } from '@/lib/cn'

export interface ErrorToastProps {
  error: AgentError
  /** Optional retry action — rendered next to the expand toggle. */
  onRetry?: () => void
}

/**
 * Layer-2 body rendered inside a Sonner toast. Expand reveals the AgentError
 * `problem / cause / fix / request_id` detail (api-shape.md §4). The outer
 * toast container is owned by Sonner; this component only renders inside
 * Sonner's `description` slot.
 */
export function ErrorToast({ error, onRetry }: ErrorToastProps) {
  const [expanded, setExpanded] = useState(false)
  const hasDetail = Boolean(error.problem || error.cause || error.fix || error.requestId)

  return (
    <div className="flex flex-col gap-1.5 text-xs">
      {hasDetail && (
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            aria-expanded={expanded}
            className="inline-flex items-center gap-1 text-muted-foreground hover:text-foreground"
          >
            {expanded ? (
              <ChevronUp className="size-3" aria-hidden />
            ) : (
              <ChevronDown className="size-3" aria-hidden />
            )}
            {expanded ? '收合詳情' : '顯示詳情'}
          </button>
          {onRetry && error.retryable && (
            <button
              type="button"
              onClick={onRetry}
              className="font-medium text-primary hover:underline"
            >
              重試
            </button>
          )}
        </div>
      )}
      <dl className={cn('grid gap-1', expanded ? 'grid-cols-[auto_1fr]' : 'hidden')}>
        <DetailRow label="錯誤代碼" value={error.code} mono />
        {error.problem && <DetailRow label="Problem" value={error.problem} />}
        {error.cause && <DetailRow label="Cause" value={error.cause} />}
        {error.fix && <DetailRow label="Fix" value={error.fix} />}
        {error.requestId && <DetailRow label="Request ID" value={error.requestId} mono />}
      </dl>
    </div>
  )
}

function DetailRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <>
      <dt className="font-medium text-muted-foreground">{label}</dt>
      <dd className={cn('break-all', mono && 'font-mono text-[11px]')}>{value}</dd>
    </>
  )
}
