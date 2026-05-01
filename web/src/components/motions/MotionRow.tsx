import { useMemo, useState } from 'react'
import { Plus } from 'lucide-react'

import {
  PRESET_LABELS,
  PRESET_MOTION_TYPES,
  type Motion,
  type MotionParentType,
  type PresetMotionType,
} from '@/api/endpoints/motions'
import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { MotionCell } from './MotionCell'
import { MotionLightbox } from './MotionLightbox'

export interface MotionRowProps {
  parentType: MotionParentType
  parentId: string
  motions: Motion[]
  isOwner: boolean
  /** Loading skeleton while the motions list is fetching. */
  isLoading?: boolean
  /**
   * Surface a fetch failure on the row so we don't silently coerce a
   * load error into "0 motions generated" — the slots themselves still
   * render (they're click-to-generate affordances) but the user sees
   * the error band so they know the displayed counts are unreliable.
   */
  errorMessage?: string | null
}

/**
 * P-05 motion strip rendered under each Base / Alias card.
 *
 * Layout: 5 fixed preset slots (filled by the matching motion if it
 * exists, otherwise an empty `+`) followed by a "自訂 motions" strip
 * with custom motions and a disabled "+ 自訂動作" button (T-039 wires
 * the Modal M-02 affordance).
 */
export function MotionRow({
  parentType,
  parentId,
  motions,
  isOwner,
  isLoading,
  errorMessage,
}: MotionRowProps) {
  const [lightboxMotion, setLightboxMotion] = useState<Motion | null>(null)
  const presetByType = useMemo(() => {
    const map = new Map<PresetMotionType, Motion>()
    for (const motion of motions) {
      if (motion.motion_type !== 'custom') {
        map.set(motion.motion_type, motion)
      }
    }
    return map
  }, [motions])
  const customMotions = useMemo(() => motions.filter((m) => m.motion_type === 'custom'), [motions])

  const presetGenerated = presetByType.size
  const customCount = customMotions.length

  return (
    <div data-testid={`motion-row-${parentType}-${parentId}`} className="flex flex-col gap-3">
      {errorMessage ? (
        <p
          role="alert"
          data-testid={`motion-row-error-${parentType}-${parentId}`}
          className="rounded border border-destructive/40 bg-destructive/5 px-2 py-1 text-xs text-destructive"
        >
          無法載入動作：{errorMessage}
        </p>
      ) : (
        <p className="text-xs text-muted-foreground">
          Motions ({presetGenerated}/5 預設 + {customCount} 自訂)
        </p>
      )}
      <ul className="flex flex-wrap gap-2" aria-label="預設動作">
        {PRESET_MOTION_TYPES.map((type) => {
          const existing = presetByType.get(type)
          return (
            <li key={type}>
              {existing ? (
                <MotionCell
                  variant="completed"
                  motion={existing}
                  isOwner={isOwner}
                  onPlay={setLightboxMotion}
                />
              ) : (
                <MotionCell
                  variant="empty"
                  slotId={type}
                  label={PRESET_LABELS[type]}
                  isOwner={isOwner}
                />
              )}
            </li>
          )
        })}
      </ul>

      <div className="flex flex-col gap-2">
        <p className="text-xs text-muted-foreground">自訂 motions</p>
        <ul className="flex flex-wrap gap-2" aria-label="自訂動作">
          {customMotions.map((motion) => (
            <li key={motion.id}>
              <MotionCell
                variant="completed"
                motion={motion}
                isOwner={isOwner}
                onPlay={setLightboxMotion}
              />
            </li>
          ))}
          <li>
            <Tooltip>
              <TooltipTrigger asChild>
                <span className="inline-flex">
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    disabled
                    data-testid={`motion-add-custom-${parentType}-${parentId}`}
                  >
                    <Plus className="size-3.5" aria-hidden />
                    自訂動作
                  </Button>
                </span>
              </TooltipTrigger>
              <TooltipContent>
                {isOwner ? 'Sprint 3 接續工作會啟用' : '僅 owner 可操作'}
              </TooltipContent>
            </Tooltip>
          </li>
        </ul>
      </div>

      {isLoading ? (
        <p className="text-xs text-muted-foreground" data-testid="motion-row-loading">
          載入動作中…
        </p>
      ) : null}

      <MotionLightbox motion={lightboxMotion} onClose={() => setLightboxMotion(null)} />
    </div>
  )
}
