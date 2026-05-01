import { Plus } from 'lucide-react'

import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'

export interface MotionGenerateButtonProps {
  /** Stable hook id (preset motion type) so tests / e2e can identify the slot. */
  slotId: string
  label: string
  isOwner: boolean
  onClick?: () => void
}

/**
 * Empty-state preset slot. Renders the dashed `+ label` cell that fires
 * `onClick` to start a motion generation. Non-owners see the same
 * affordance disabled with a `僅 owner 可操作` tooltip; owners get the
 * raw button (no tooltip wrapper) so the click target stays a single
 * focusable element.
 */
export function MotionGenerateButton({
  slotId,
  label,
  isOwner,
  onClick,
}: MotionGenerateButtonProps) {
  const button = (
    <button
      type="button"
      disabled={!isOwner}
      onClick={isOwner ? onClick : undefined}
      data-testid={`motion-cell-empty-${slotId}`}
      data-slot-id={slotId}
      aria-label={`生成 ${label}`}
      className="flex h-16 w-16 flex-col items-center justify-center gap-1 rounded border border-dashed border-border/60 bg-muted/30 text-xs text-muted-foreground transition hover:border-primary/60 hover:text-foreground disabled:cursor-not-allowed disabled:hover:border-border/60 disabled:hover:text-muted-foreground"
    >
      <Plus className="size-3.5" aria-hidden />
      <span>{label}</span>
    </button>
  )
  if (isOwner) return button
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex">{button}</span>
      </TooltipTrigger>
      <TooltipContent>僅 owner 可操作</TooltipContent>
    </Tooltip>
  )
}
