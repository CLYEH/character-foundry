import { Eye } from 'lucide-react'

import type { Base } from '@/api/endpoints/characters'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'

export interface BaseCardProps {
  base: Base
  onViewPrompt: () => void
}

/**
 * Hero card on `/characters/:id` showing the immutable Base image plus
 * the "查看完整 prompt" affordance. Sprint 2 keeps this read-only — alias
 * / motion strips render alongside as separate components.
 */
export function BaseCard({ base, onViewPrompt }: BaseCardProps) {
  const src = base.image_url ?? base.thumbnail_url
  return (
    <Card data-testid="base-card" className="overflow-hidden p-0">
      <div className="grid grid-cols-1 gap-0 md:grid-cols-[16rem_1fr]">
        <div className="aspect-square w-full bg-muted md:aspect-auto md:h-full">
          {src ? (
            <img
              src={src}
              alt="Base"
              loading="lazy"
              className="h-full w-full object-cover"
              data-testid="base-card-image"
            />
          ) : (
            <div className="flex h-full w-full items-center justify-center text-xs text-muted-foreground">
              無預覽
            </div>
          )}
        </div>
        <div className="flex flex-col gap-3 p-4">
          <div>
            <p className="text-xs uppercase tracking-wide text-muted-foreground">Base</p>
            <p className="text-sm text-muted-foreground">
              Base 是這個角色的基底樣貌，建立後不可修改。
            </p>
          </div>
          <div>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={onViewPrompt}
              data-testid="base-view-prompt"
            >
              <Eye className="size-3.5" aria-hidden />
              查看完整 prompt
            </Button>
          </div>
        </div>
      </div>
    </Card>
  )
}
