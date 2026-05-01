import { useEffect, useRef, useState } from 'react'
import { Loader2, MoreHorizontal, RotateCcw, Trash2, X } from 'lucide-react'

import type { Motion } from '@/api/endpoints/motions'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { MotionGenerateButton } from './MotionGenerateButton'

/**
 * Motion cells share a 64×64 footprint across all 5 lifecycle states.
 * Variants are split as a discriminated union so each state ships a
 * minimal, type-checked prop set — TaskStatusBadge / TaskProgress
 * (component-map §4.10) is intentionally inlined here because the cell
 * is too small for the full progress-row layout.
 */
export interface MotionCellEmptyProps {
  variant: 'empty'
  label: string
  /** Stable hook id so tests / e2e can identify the slot (preset motion type). */
  slotId: string
  isOwner: boolean
  onTrigger?: () => void
}

export interface MotionCellQueuedProps {
  variant: 'queued'
  slotId: string
  label: string
  isOwner: boolean
  /** SSE-supplied queue position; null → indeterminate spinner per UX §5.1. */
  queuePosition: number | null
  onCancel?: () => void
}

export interface MotionCellRunningProps {
  variant: 'running'
  slotId: string
  label: string
  isOwner: boolean
  /** 0–1; when null or < 0.05 we render an indeterminate spinner instead of a bar. */
  progress: number | null
  onCancel?: () => void
}

export interface MotionCellCancellingProps {
  variant: 'cancelling'
  slotId: string
  label: string
}

export interface MotionCellCompletedProps {
  variant: 'completed'
  motion: Motion
  isOwner: boolean
  onPlay: (motion: Motion) => void
  onDelete?: (motion: Motion) => void
}

export interface MotionCellFailedProps {
  variant: 'failed'
  slotId: string
  label: string
  isOwner: boolean
  errorMessage: string
  onRetry?: () => void
  onDismiss?: () => void
}

export type MotionCellProps =
  | MotionCellEmptyProps
  | MotionCellQueuedProps
  | MotionCellRunningProps
  | MotionCellCancellingProps
  | MotionCellCompletedProps
  | MotionCellFailedProps

export function MotionCell(props: MotionCellProps) {
  switch (props.variant) {
    case 'empty':
      return (
        <MotionGenerateButton
          slotId={props.slotId}
          label={props.label}
          isOwner={props.isOwner}
          onClick={props.onTrigger}
        />
      )
    case 'queued':
      return <QueuedCell {...props} />
    case 'running':
      return <RunningCell {...props} />
    case 'cancelling':
      return <CancellingCell {...props} />
    case 'completed':
      return <CompletedCell {...props} />
    case 'failed':
      return <FailedCell {...props} />
  }
}

function QueuedCell({ slotId, label, isOwner, queuePosition, onCancel }: MotionCellQueuedProps) {
  const positionLabel =
    typeof queuePosition === 'number' && queuePosition > 0 ? `#${queuePosition} in queue` : '排隊中'
  return (
    <div
      data-testid={`motion-cell-queued-${slotId}`}
      data-slot-id={slotId}
      className="relative flex h-16 w-16 flex-col items-center justify-center gap-1 rounded border border-border bg-muted/40 text-[10px] text-muted-foreground"
      aria-label={`${label} 排隊中`}
    >
      <Loader2 className="size-4 animate-spin" aria-hidden />
      <span
        className="line-clamp-1 px-1 text-center"
        data-testid={`motion-cell-queued-label-${slotId}`}
      >
        {positionLabel}
      </span>
      {isOwner && onCancel ? <CancelOverlay slotId={slotId} onCancel={onCancel} /> : null}
    </div>
  )
}

function RunningCell({ slotId, label, isOwner, progress, onCancel }: MotionCellRunningProps) {
  const showBar = typeof progress === 'number' && progress >= 0.05
  const percent = showBar ? Math.min(100, Math.round((progress ?? 0) * 100)) : 0
  return (
    <div
      data-testid={`motion-cell-running-${slotId}`}
      data-slot-id={slotId}
      className="relative flex h-16 w-16 flex-col items-center justify-center gap-1 rounded border border-border bg-muted/40 text-[10px] text-muted-foreground"
      aria-label={`${label} 生成中`}
    >
      {showBar ? (
        <>
          <Loader2 className="size-3.5 animate-spin" aria-hidden />
          <Progress
            value={percent}
            data-testid={`motion-cell-progress-${slotId}`}
            className="h-1 w-12"
          />
          <span className="text-[9px]">{percent}%</span>
        </>
      ) : (
        <>
          <Loader2 className="size-4 animate-spin" aria-hidden />
          <span>生成中</span>
        </>
      )}
      {isOwner && onCancel ? <CancelOverlay slotId={slotId} onCancel={onCancel} /> : null}
    </div>
  )
}

function CancellingCell({ slotId, label }: MotionCellCancellingProps) {
  return (
    <div
      data-testid={`motion-cell-cancelling-${slotId}`}
      data-slot-id={slotId}
      className="flex h-16 w-16 flex-col items-center justify-center gap-1 rounded border border-border bg-muted/40 text-[10px] text-muted-foreground"
      aria-label={`${label} 取消中`}
    >
      <Loader2 className="size-4 animate-spin" aria-hidden />
      <span>取消中…</span>
    </div>
  )
}

function CompletedCell({ motion, isOwner, onPlay, onDelete }: MotionCellCompletedProps) {
  const thumb = motion.thumbnail_url
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement | null>(null)

  // Click-outside / Escape close the menu so the cell behaves like a
  // proper popover affordance without bringing in Radix's portal +
  // pointer-event handling (which we'd then have to special-case in
  // jsdom tests).
  useEffect(() => {
    if (!menuOpen) return
    const handlePointer = (event: PointerEvent) => {
      const target = event.target
      if (target instanceof Node && menuRef.current && !menuRef.current.contains(target)) {
        setMenuOpen(false)
      }
    }
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setMenuOpen(false)
    }
    window.addEventListener('pointerdown', handlePointer)
    window.addEventListener('keydown', handleKey)
    return () => {
      window.removeEventListener('pointerdown', handlePointer)
      window.removeEventListener('keydown', handleKey)
    }
  }, [menuOpen])

  return (
    <div className="group relative h-16 w-16">
      <button
        type="button"
        data-testid={`motion-cell-completed-${motion.id}`}
        data-motion-id={motion.id}
        onClick={() => onPlay(motion)}
        className="flex h-full w-full flex-col items-center justify-end overflow-hidden rounded border border-border bg-muted text-[10px] font-medium text-foreground"
        aria-label={`播放 ${motion.name}`}
      >
        {thumb ? (
          <img
            src={thumb}
            alt=""
            aria-hidden
            className="absolute inset-0 h-full w-full object-cover transition group-hover:opacity-80"
          />
        ) : (
          <span className="absolute inset-0 flex items-center justify-center text-muted-foreground">
            🎬
          </span>
        )}
        <span className="relative z-10 line-clamp-1 w-full bg-background/80 px-1 py-0.5 text-center">
          {motion.name}
        </span>
      </button>
      {isOwner && onDelete ? (
        <div ref={menuRef} className="absolute right-0.5 top-0.5 z-20">
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation()
              setMenuOpen((v) => !v)
            }}
            data-testid={`motion-cell-menu-${motion.id}`}
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            aria-label={`${motion.name} 操作選單`}
            className="flex size-5 items-center justify-center rounded bg-background/85 text-muted-foreground shadow-sm transition hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <MoreHorizontal className="size-3.5" aria-hidden />
          </button>
          {menuOpen ? (
            <div
              role="menu"
              data-testid={`motion-cell-menu-list-${motion.id}`}
              className="absolute right-0 top-full z-30 mt-1 min-w-[8rem] overflow-hidden rounded-md border border-border bg-popover p-1 text-sm text-popover-foreground shadow-md"
            >
              <button
                type="button"
                role="menuitem"
                data-testid={`motion-cell-replay-${motion.id}`}
                onClick={() => {
                  setMenuOpen(false)
                  onPlay(motion)
                }}
                className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left hover:bg-accent hover:text-accent-foreground"
              >
                <RotateCcw className="size-3.5" aria-hidden />
                重新播放
              </button>
              <button
                type="button"
                role="menuitem"
                data-testid={`motion-cell-delete-${motion.id}`}
                onClick={() => {
                  setMenuOpen(false)
                  onDelete(motion)
                }}
                className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left text-destructive hover:bg-destructive/10"
              >
                <Trash2 className="size-3.5" aria-hidden />
                刪除
              </button>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

function FailedCell({
  slotId,
  label,
  isOwner,
  errorMessage,
  onRetry,
  onDismiss,
}: MotionCellFailedProps) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <div
          data-testid={`motion-cell-failed-${slotId}`}
          data-slot-id={slotId}
          aria-label={`${label} 生成失敗：${errorMessage}`}
          className="relative flex h-16 w-16 flex-col items-center justify-center gap-1 rounded border border-destructive/60 bg-destructive/5 text-[10px] text-destructive"
        >
          <span aria-hidden className="text-base font-bold">
            !
          </span>
          <span>生成失敗</span>
          {isOwner ? (
            <div className="absolute inset-x-0 bottom-0 flex justify-between gap-0.5 bg-background/85 px-1 py-0.5">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-4 w-full px-1 text-[9px]"
                onClick={onRetry}
                data-testid={`motion-cell-retry-${slotId}`}
                disabled={!onRetry}
              >
                重試
              </Button>
              {onDismiss ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="h-4 px-1 text-[9px]"
                  onClick={onDismiss}
                  data-testid={`motion-cell-dismiss-${slotId}`}
                  aria-label="關閉錯誤"
                >
                  <X className="size-3" aria-hidden />
                </Button>
              ) : null}
            </div>
          ) : null}
        </div>
      </TooltipTrigger>
      <TooltipContent data-testid={`motion-cell-failed-tooltip-${slotId}`}>
        {errorMessage}
      </TooltipContent>
    </Tooltip>
  )
}

function CancelOverlay({ slotId, onCancel }: { slotId: string; onCancel: () => void }) {
  return (
    <button
      type="button"
      onClick={onCancel}
      data-testid={`motion-cell-cancel-${slotId}`}
      aria-label="取消生成"
      className="absolute right-0.5 top-0.5 flex size-4 items-center justify-center rounded bg-background/85 text-muted-foreground transition hover:text-foreground"
    >
      <X className="size-3" aria-hidden />
    </button>
  )
}
