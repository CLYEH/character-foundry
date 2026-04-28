import { Plus } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'

/**
 * Empty placeholder for the Aliases section on the character detail page.
 * The "+ 新增 Alias" button is disabled in Sprint 2 — Sprint 3 wires it
 * to the alias creation flow (P-06).
 */
export function AliasEmptyState() {
  return (
    <div
      data-testid="alias-empty-state"
      className="flex flex-col items-center justify-center gap-3 rounded-md border border-dashed border-border/60 bg-muted/30 px-6 py-10 text-center"
    >
      <p className="text-sm font-medium">Base 是基礎，來加些變體吧</p>
      <p className="text-xs text-muted-foreground">
        Alias 是同一角色的不同造型 / 服裝，永遠基於 Base 生成。
      </p>
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="inline-flex">
            <Button type="button" variant="secondary" size="sm" disabled>
              <Plus className="size-3.5" aria-hidden />
              新增 Alias
            </Button>
          </span>
        </TooltipTrigger>
        <TooltipContent>Sprint 3 會開放</TooltipContent>
      </Tooltip>
    </div>
  )
}
