import { Link } from 'react-router'

import type { Character } from '@/api/endpoints/characters'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { cn } from '@/lib/cn'

export interface CharacterCardProps {
  character: Character
  isOwner: boolean
}

export function CharacterCard({ character, isOwner }: CharacterCardProps) {
  // Wrapping a `<button>` inside an `<a>` is invalid HTML, so the Copy
  // button overlay sits as a sibling positioned above the link surface
  // instead of nested inside it.
  return (
    <div className="relative">
      <Link
        to={`/characters/${character.id}`}
        className="block rounded-xl outline-none focus-visible:ring-2 focus-visible:ring-ring"
        aria-label={`開啟角色 ${character.name}`}
      >
        <Card className="gap-0 overflow-hidden py-0 transition hover:shadow-md">
          <div className="aspect-[3/4] w-full bg-muted">
            {character.base_thumbnail_url ? (
              // alt="" — decorative; character.name is the visible label
              // below, so giving the img a name would double-read in SR.
              <img
                src={character.base_thumbnail_url}
                alt=""
                loading="lazy"
                className="h-full w-full object-cover"
              />
            ) : null}
          </div>
          <div className="flex flex-col gap-1 px-4 py-3">
            <h3 className="truncate text-sm font-semibold">{character.name}</h3>
            <p className="text-xs text-muted-foreground">
              {character.alias_count} aliases · {character.motion_count} motions
            </p>
            {!isOwner && (
              <p className="truncate text-xs text-muted-foreground">by {character.owner.name}</p>
            )}
          </div>
        </Card>
      </Link>
      {!isOwner && (
        <div className="absolute right-2 top-2">
          <Tooltip>
            <TooltipTrigger asChild>
              {/* The disabled button is wrapped so the tooltip still
                  fires on hover (Radix forwards events through the
                  span). */}
              <span className={cn('inline-flex')}>
                <Button
                  type="button"
                  variant="secondary"
                  size="xs"
                  disabled
                  aria-label={`複製 ${character.name}`}
                  // TODO(Sprint 4): when this button is enabled, the
                  // onClick handler must call e.stopPropagation() (and
                  // e.preventDefault() if it sits over the Link) so the
                  // surrounding card link doesn't navigate as a side-
                  // effect of clicking Copy.
                >
                  Copy
                </Button>
              </span>
            </TooltipTrigger>
            <TooltipContent side="left">Sprint 4 再做</TooltipContent>
          </Tooltip>
        </div>
      )}
    </div>
  )
}
