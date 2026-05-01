import { AlertCircle, Loader2, X } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import type { CheckpointCardModel } from './types'

const PROGRESS_VISIBLE_THRESHOLD = 0.05

export interface CheckpointCardProps {
  model: CheckpointCardModel
  onCancel: (taskId: string) => void
  onRetry: (checkpointId: string) => void
  onRemix: (checkpointId: string, sequence: number | null) => void
  onSelectAsBase: (checkpointId: string) => void
  onOpenLightbox: (checkpointId: string) => void
}

/**
 * Single checkpoint cell on the right rail. The card derives every visual
 * cue from `model.status` so the page only needs to keep `model` in sync —
 * no other prop drives layout. Progress-bar threshold (≥ 0.05) follows UX
 * §6 row 6: below it we use indeterminate spinner instead.
 */
export function CheckpointCard({
  model,
  onCancel,
  onRetry,
  onRemix,
  onSelectAsBase,
  onOpenLightbox,
}: CheckpointCardProps) {
  const sequenceLabel = model.sequence !== null ? `#${model.sequence}` : '生成中'
  // Error lives on the model (not on event) because cancel-mutation synthetic
  // events for `too_late_failed` never enter the SSE events map. The model is
  // the merged source of truth — see buildCardModels in CreationSessionPage.
  const errorMessage = model.error?.message ?? null
  const queuePosition = model.event?.queue_position ?? null
  const progress = model.event?.progress ?? null
  const cancelling =
    model.cancelRequested && model.status !== 'cancelled' && model.status !== 'completed'

  return (
    <article
      data-testid={`checkpoint-card-${model.checkpointId}`}
      data-status={model.status}
      className="flex flex-col gap-2 rounded-md border border-border/60 bg-card p-3 shadow-sm"
    >
      <header className="flex items-center justify-between text-xs text-muted-foreground">
        <span data-testid="checkpoint-sequence">{sequenceLabel}</span>
        <span data-testid="checkpoint-status">{statusLabel(model)}</span>
      </header>

      <div className="relative aspect-[2/3] w-full overflow-hidden rounded bg-muted">
        {model.status === 'completed' ? (
          // Always render a click target so the lightbox is reachable even if
          // the thumbnail URL is null (Checkpoint DTO allows it). Fall back to
          // the full-resolution `output_image_url`, then to a "no preview"
          // placeholder if both are missing.
          <button
            type="button"
            className="block size-full"
            onClick={() => onOpenLightbox(model.checkpointId)}
            aria-label={`開啟 Checkpoint ${sequenceLabel} 大圖`}
          >
            {completedImageSrc(model) ? (
              <img
                src={completedImageSrc(model) as string}
                alt={`Checkpoint ${sequenceLabel}`}
                className="size-full object-contain"
                loading="lazy"
              />
            ) : (
              <div className="flex size-full items-center justify-center text-xs text-muted-foreground">
                無預覽
              </div>
            )}
          </button>
        ) : model.status === 'failed' ? (
          <div className="flex size-full flex-col items-center justify-center gap-1 text-destructive">
            <AlertCircle className="size-6" aria-hidden />
            <span className="text-xs">失敗</span>
          </div>
        ) : (
          <div className="flex size-full flex-col items-center justify-center gap-2 text-muted-foreground">
            <Loader2 className="size-6 animate-spin" aria-hidden />
            {model.status === 'queued' && queuePosition !== null ? (
              <span className="text-xs">#{queuePosition} in queue</span>
            ) : null}
          </div>
        )}
      </div>

      {model.status === 'running' && progress !== null && progress >= PROGRESS_VISIBLE_THRESHOLD ? (
        <Progress value={progress * 100} aria-label="生成進度" />
      ) : null}

      {model.status === 'failed' && errorMessage ? (
        <p data-testid="checkpoint-error-message" role="alert" className="text-xs text-destructive">
          {errorMessage}
        </p>
      ) : null}

      <footer className="flex flex-wrap gap-2 pt-1">
        {(model.status === 'queued' || model.status === 'running') && model.taskId && (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={() => model.taskId && onCancel(model.taskId)}
            disabled={cancelling}
          >
            <X className="size-3" aria-hidden />
            {cancelling ? '取消中…' : '取消'}
          </Button>
        )}
        {model.status === 'completed' && (
          <>
            <Button
              type="button"
              size="sm"
              variant="secondary"
              onClick={() => onRemix(model.checkpointId, model.sequence)}
            >
              用這張再改
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => onSelectAsBase(model.checkpointId)}
            >
              選作 Base
            </Button>
          </>
        )}
        {model.status === 'failed' && (
          <Button
            type="button"
            size="sm"
            variant="secondary"
            onClick={() => onRetry(model.checkpointId)}
            disabled={!model.request}
          >
            重試
          </Button>
        )}
      </footer>
    </article>
  )
}

function completedImageSrc(model: CheckpointCardModel): string | null {
  return model.checkpoint?.thumbnail_url ?? model.checkpoint?.output_image_url ?? null
}

function statusLabel(model: CheckpointCardModel): string {
  switch (model.status) {
    case 'queued':
      return '排隊中'
    case 'running':
      return '生成中'
    case 'completed':
      return '已完成'
    case 'failed':
      return '失敗'
    case 'cancelled':
      return '已取消'
  }
}
