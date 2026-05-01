import { Plus } from 'lucide-react'
import { Link } from 'react-router'

import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'

export interface AliasEmptyStateProps {
  characterId: string
  /** Owners get an enabled "+ 新增 Alias" link; viewers see a disabled
   *  button with a tooltip. */
  isOwner: boolean
}

/**
 * Empty placeholder for the Aliases section on the character detail page.
 * For owners the "+ 新增 Alias" button is a real link to the alias edit
 * route (P-06). Non-owners see the same hint copy with a disabled CTA.
 */
export function AliasEmptyState({ characterId, isOwner }: AliasEmptyStateProps) {
  return (
    <div
      data-testid="alias-empty-state"
      className="flex flex-col items-center justify-center gap-3 rounded-md border border-dashed border-border/60 bg-muted/30 px-6 py-10 text-center"
    >
      <p className="text-sm font-medium">Base 是基礎，來加些變體吧</p>
      <p className="text-xs text-muted-foreground">
        Alias 是同一角色的不同造型 / 服裝，永遠基於 Base 生成。
      </p>
      {isOwner ? (
        <Button asChild variant="secondary" size="sm" data-testid="alias-empty-create-cta">
          <Link to={`/characters/${characterId}/aliases/new`}>
            <Plus className="size-3.5" aria-hidden />
            新增 Alias
          </Link>
        </Button>
      ) : (
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="inline-flex">
              <Button type="button" variant="secondary" size="sm" disabled>
                <Plus className="size-3.5" aria-hidden />
                新增 Alias
              </Button>
            </span>
          </TooltipTrigger>
          <TooltipContent>僅 owner 可操作</TooltipContent>
        </Tooltip>
      )}
    </div>
  )
}
