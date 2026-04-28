import { Loader2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { FREEFORM_MAX_LENGTH } from '@/constants/menu_options'
import { MAX_REFERENCE_IMAGES, type ReferenceImageItem } from '@/hooks/useReferenceUpload'
import { ReferenceImageDropzone } from './ReferenceImageDropzone'
import { ReferenceImagePreview } from './ReferenceImagePreview'

export interface ReferenceInputPanelProps {
  items: ReferenceImageItem[]
  freeformNote: string
  remixSequence: number | null
  hasAnyCheckpoint: boolean
  isSubmitting: boolean
  isUploading: boolean
  hasReferenceReady: boolean
  onAddFiles: (files: File[]) => void
  onRemoveImage: (localId: string) => void
  onRetryImage: (localId: string) => void
  onFreeformChange: (value: string) => void
  onGenerate: () => void
  onRetry: () => void
  onReset: () => void
  onAdvancedView: () => void
}

/**
 * Left rail of P-04 (reference mode). Mirrors `TemplateInputPanel`'s
 * shape so `CreationSessionPage` only swaps the panel — every action
 * button still funnels through the same generate / retry / reset
 * callbacks. Empty state ([生成] disabled) requires both an uploaded
 * reference *and* an absence of in-flight uploads, since we never want
 * to submit with a `reference_image_id` that hasn't settled yet.
 */
export function ReferenceInputPanel(props: ReferenceInputPanelProps) {
  const {
    items,
    freeformNote,
    remixSequence,
    hasAnyCheckpoint,
    isSubmitting,
    isUploading,
    hasReferenceReady,
    onAddFiles,
    onRemoveImage,
    onRetryImage,
    onFreeformChange,
    onGenerate,
    onRetry,
    onReset,
    onAdvancedView,
  } = props

  const noteLength = freeformNote.length
  const overLimit = noteLength > FREEFORM_MAX_LENGTH
  const generateDisabled = isSubmitting || isUploading || !hasReferenceReady || overLimit
  const remaining = MAX_REFERENCE_IMAGES - items.length

  return (
    <aside
      className="flex w-full max-w-sm flex-col gap-4 border-r border-border/50 pr-6"
      aria-label="輸入控制"
    >
      {remixSequence !== null && (
        <div
          data-testid="remix-context-header"
          className="rounded-md border border-primary/40 bg-primary/5 px-3 py-2 text-xs text-primary"
        >
          基於 Ckpt #{remixSequence}
        </div>
      )}

      <div className="flex flex-col gap-2">
        <div className="text-sm font-medium leading-none">
          參考圖（{items.length}/{MAX_REFERENCE_IMAGES}）
        </div>
        <ReferenceImageDropzone
          remaining={remaining}
          onFiles={onAddFiles}
          disabled={isSubmitting}
        />
        {items.length > 0 && (
          <div
            data-testid="reference-image-grid"
            className="grid grid-cols-3 gap-2"
          >
            {items.map((item) => (
              <ReferenceImagePreview
                key={item.localId}
                item={item}
                onRemove={onRemoveImage}
                onRetry={onRetryImage}
              />
            ))}
          </div>
        )}
      </div>

      <div className="flex flex-col gap-1">
        <Label htmlFor="freeform-note">自由補述</Label>
        <Textarea
          id="freeform-note"
          rows={4}
          maxLength={FREEFORM_MAX_LENGTH}
          value={freeformNote}
          onChange={(e) => onFreeformChange(e.target.value)}
          aria-invalid={overLimit ? 'true' : undefined}
          aria-describedby="freeform-note-hint"
        />
        <div
          id="freeform-note-hint"
          className="flex items-center justify-between text-xs text-muted-foreground"
        >
          <span>用中文描述細節，AI 會自動翻譯與重組</span>
          <span data-testid="freeform-counter">
            {noteLength}/{FREEFORM_MAX_LENGTH}
          </span>
        </div>
      </div>

      <div className="flex flex-col gap-2">
        <Button type="button" disabled={generateDisabled} onClick={onGenerate}>
          {isSubmitting && <Loader2 className="size-4 animate-spin" aria-hidden />}
          {isSubmitting ? '送出中…' : '生成新候選'}
        </Button>
        <Button
          type="button"
          variant="secondary"
          disabled={!hasAnyCheckpoint || isSubmitting}
          onClick={onRetry}
        >
          用同設定再試一次
        </Button>
        <Button type="button" variant="ghost" onClick={onReset} disabled={isSubmitting}>
          從頭
        </Button>
        <Button type="button" variant="ghost" onClick={onAdvancedView}>
          進階檢視 Prompt
        </Button>
      </div>
    </aside>
  )
}
