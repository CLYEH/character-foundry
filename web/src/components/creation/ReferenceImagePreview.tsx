import { AlertCircle, Loader2, X } from 'lucide-react'

import { Button } from '@/components/ui/button'
import type { ReferenceImageItem } from '@/hooks/useReferenceUpload'

export interface ReferenceImagePreviewProps {
  item: ReferenceImageItem
  onRemove: (localId: string) => void
  onRetry: (localId: string) => void
}

export function ReferenceImagePreview({ item, onRemove, onRetry }: ReferenceImagePreviewProps) {
  return (
    <article
      data-testid={`reference-image-preview-${item.localId}`}
      data-status={item.status}
      className="relative aspect-square overflow-hidden rounded-md border border-border/60 bg-muted"
    >
      <img
        src={item.previewUrl}
        alt={item.fileName}
        className="absolute inset-0 size-full object-cover"
      />

      {item.status === 'uploading' && (
        <div className="absolute inset-0 flex items-center justify-center bg-background/60 text-xs text-muted-foreground">
          <Loader2 className="size-4 animate-spin" aria-hidden />
          <span className="ml-1">上傳中…</span>
        </div>
      )}

      {item.status === 'error' && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-background/80 px-2 text-center text-xs text-destructive">
          <AlertCircle className="size-4" aria-hidden />
          <span data-testid="reference-image-error">{item.errorMessage ?? '上傳失敗'}</span>
          <Button type="button" size="sm" variant="secondary" onClick={() => onRetry(item.localId)}>
            重試
          </Button>
        </div>
      )}

      <Button
        type="button"
        variant="secondary"
        size="icon"
        className="absolute right-1 top-1 size-6"
        aria-label={`移除 ${item.fileName}`}
        data-testid={`reference-image-remove-${item.localId}`}
        onClick={() => onRemove(item.localId)}
      >
        <X className="size-3" aria-hidden />
      </Button>
    </article>
  )
}
