import { Plus } from 'lucide-react'

import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'

const PRESET_LABELS = ['招手', '點頭', '手勢', '開心', '待機'] as const

/**
 * Empty Motions strip on the character detail page. Sprint 2 renders the
 * 5 preset placeholders disabled with a Sprint 3 tooltip. Clicking a
 * preset slot is a Sprint 3 ticket — for now they're inert squares.
 */
export function MotionEmptyStrip() {
  return (
    <div data-testid="motion-empty-strip" className="flex flex-col gap-2">
      <p className="text-xs text-muted-foreground">動作會在這裡出現</p>
      <ul className="flex flex-wrap gap-2">
        {PRESET_LABELS.map((label) => (
          <li key={label}>
            <Tooltip>
              <TooltipTrigger asChild>
                <span className="inline-flex">
                  <button
                    type="button"
                    disabled
                    aria-label={`生成 ${label}（Sprint 3）`}
                    className="flex h-16 w-16 cursor-not-allowed flex-col items-center justify-center gap-1 rounded border border-dashed border-border/60 bg-muted/30 text-xs text-muted-foreground"
                  >
                    <Plus className="size-3.5" aria-hidden />
                    <span>{label}</span>
                  </button>
                </span>
              </TooltipTrigger>
              <TooltipContent>Sprint 3 會開放</TooltipContent>
            </Tooltip>
          </li>
        ))}
      </ul>
    </div>
  )
}
