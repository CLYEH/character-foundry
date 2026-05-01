import { Plus } from 'lucide-react'

import type { Motion } from '@/api/endpoints/motions'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'

export interface MotionCellEmptyProps {
  variant: 'empty'
  label: string
  isOwner: boolean
  /** Stable hook id so tests / e2e can identify the slot (e.g. preset key). */
  slotId: string
}

export interface MotionCellCompletedProps {
  variant: 'completed'
  motion: Motion
  isOwner: boolean
  onPlay: (motion: Motion) => void
}

export type MotionCellProps = MotionCellEmptyProps | MotionCellCompletedProps

/**
 * One slot in the MotionRow strip. Empty + completed states only — the
 * mutation that moves a slot from empty → queued → running → completed
 * is owned by T-038.
 *
 * Empty cells are deliberately disabled in this ticket: the Sprint 3
 * tooltip ("Sprint 3 接續工作會啟用") signals that click-to-generate is
 * not yet wired. T-038 will replace the disabled `<button>` with the
 * real generation trigger.
 */
export function MotionCell(props: MotionCellProps) {
  if (props.variant === 'empty') {
    const { label, isOwner, slotId } = props
    const tooltip = isOwner ? 'Sprint 3 接續工作會啟用' : '僅 owner 可操作'
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="inline-flex">
            <button
              type="button"
              disabled
              data-testid={`motion-cell-empty-${slotId}`}
              data-slot-id={slotId}
              aria-label={`生成 ${label}`}
              className="flex h-16 w-16 cursor-not-allowed flex-col items-center justify-center gap-1 rounded border border-dashed border-border/60 bg-muted/30 text-xs text-muted-foreground"
            >
              <Plus className="size-3.5" aria-hidden />
              <span>{label}</span>
            </button>
          </span>
        </TooltipTrigger>
        <TooltipContent>{tooltip}</TooltipContent>
      </Tooltip>
    )
  }

  const { motion, onPlay } = props
  const thumb = motion.thumbnail_url
  return (
    <button
      type="button"
      data-testid={`motion-cell-completed-${motion.id}`}
      data-motion-id={motion.id}
      onClick={() => onPlay(motion)}
      className="group relative flex h-16 w-16 flex-col items-center justify-end overflow-hidden rounded border border-border bg-muted text-[10px] font-medium text-foreground"
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
  )
}
