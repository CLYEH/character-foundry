import { Badge } from '@/components/ui/badge'

export interface MaskPreviewBadgeProps {
  /** Mask coverage as percent of total base image area (0–100), or null when no mask exists. */
  coveragePercent: number | null
  /** When the canvas is disabled the right-column row stays visible but mutes. */
  disabled?: boolean
}

export function MaskPreviewBadge({ coveragePercent, disabled }: MaskPreviewBadgeProps) {
  if (disabled) {
    return (
      <Badge variant="outline" data-testid="mask-preview-badge" className="text-muted-foreground">
        Inpaint 未啟用
      </Badge>
    )
  }
  if (coveragePercent === null) {
    return (
      <Badge variant="outline" data-testid="mask-preview-badge">
        尚未繪製 mask
      </Badge>
    )
  }
  // Round so the badge text doesn't jitter on every micro-stroke. `< 1`
  // shows as "<1%" so a tiny dot doesn't read as "0%" (which the user
  // would interpret as "the mask isn't being captured").
  const rounded = coveragePercent < 1 ? '<1' : `${Math.round(coveragePercent)}`
  return (
    <Badge variant="default" data-testid="mask-preview-badge">
      Mask 覆蓋 {rounded}%
    </Badge>
  )
}
