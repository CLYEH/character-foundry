import { Loader2 } from 'lucide-react'

import type { AspectRatio } from '@/api/endpoints/checkpoints'
import { ASPECT_RATIO_OPTIONS } from '@/components/creation/aspectRatio'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import {
  FREEFORM_MAX_LENGTH,
  MENU_FIELDS,
  type MenuKey,
  type MenuSelections,
} from '@/constants/menu_options'

export interface TemplateInputPanelProps {
  menuSelections: MenuSelections
  freeformNote: string
  aspectRatio: AspectRatio
  remixSequence: number | null
  hasAnyCheckpoint: boolean
  isSubmitting: boolean
  onMenuChange: (key: MenuKey, value: string) => void
  onFreeformChange: (value: string) => void
  onAspectRatioChange: (value: AspectRatio) => void
  onGenerate: () => void
  onRetry: () => void
  onReset: () => void
  onAdvancedView: () => void
}

/**
 * Left rail of P-04 (template mode). Owns no async state of its own — every
 * input value and every button outcome is lifted to `CreationSessionPage` so
 * placeholder cards, remix context, and the SSE stream stay in one source of
 * truth. The empty-string sentinel on `<Select>` avoids passing `undefined`
 * (Radix treats empty string as "no value picked").
 */
export function TemplateInputPanel(props: TemplateInputPanelProps) {
  const {
    menuSelections,
    freeformNote,
    aspectRatio,
    remixSequence,
    hasAnyCheckpoint,
    isSubmitting,
    onMenuChange,
    onFreeformChange,
    onAspectRatioChange,
    onGenerate,
    onRetry,
    onReset,
    onAdvancedView,
  } = props

  const noteLength = freeformNote.length
  const overLimit = noteLength > FREEFORM_MAX_LENGTH
  const generateDisabled = isSubmitting || overLimit

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

      <div className="flex flex-col gap-3">
        {MENU_FIELDS.map((field) => (
          <div key={field.key} className="flex flex-col gap-1">
            <Label htmlFor={`menu-${field.key}`}>{field.label_zh}</Label>
            <Select
              value={menuSelections[field.key] ?? ''}
              onValueChange={(value) => onMenuChange(field.key, value)}
            >
              <SelectTrigger id={`menu-${field.key}`} className="w-full">
                <SelectValue placeholder="（不指定）" />
              </SelectTrigger>
              <SelectContent>
                {field.options.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label_zh}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        ))}
      </div>

      <div className="flex flex-col gap-1">
        <Label htmlFor="aspect-ratio">畫面比例</Label>
        <Select
          value={aspectRatio}
          onValueChange={(value) => onAspectRatioChange(value as AspectRatio)}
        >
          <SelectTrigger id="aspect-ratio" className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {ASPECT_RATIO_OPTIONS.map((opt) => (
              <SelectItem key={opt.value} value={opt.value}>
                {opt.label_zh}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
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
