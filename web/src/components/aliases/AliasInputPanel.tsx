import { Loader2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { ReferenceImageDropzone } from '@/components/creation/ReferenceImageDropzone'
import { ReferenceImagePreview } from '@/components/creation/ReferenceImagePreview'
import { MAX_REFERENCE_IMAGES, type ReferenceImageItem } from '@/hooks/useReferenceUpload'

import { MaskPreviewBadge } from './MaskPreviewBadge'

const FREEFORM_NOTE_MAX = 200
const ALIAS_NAME_MAX = 50

export interface AliasInputPanelProps {
  // ---- form values ----
  aliasName: string
  freeformNote: string
  // ---- section toggles (UI affordance only — submit validation still
  //      checks input value presence, not which sections are open) ----
  textEnabled: boolean
  referenceEnabled: boolean
  inpaintEnabled: boolean
  // ---- reference upload state (mirrors creation-session reference panel) ----
  referenceItems: ReferenceImageItem[]
  isUploading: boolean
  // ---- inpaint state (drawn on the canvas in the left column) ----
  maskCoveragePercent: number | null
  // ---- submit state ----
  isSubmitting: boolean
  /**
   * Disables the generate button when no input section has content. UI hint
   * text below the button explains why so the user isn't left guessing.
   */
  canSubmit: boolean
  /** True while POST /tasks/{id}/cancel is in flight — disables the cancel button to avoid spam clicks. */
  isCancelling: boolean
  // ---- callbacks ----
  onAliasNameChange: (value: string) => void
  onFreeformChange: (value: string) => void
  onToggleText: (enabled: boolean) => void
  onToggleReference: (enabled: boolean) => void
  onToggleInpaint: (enabled: boolean) => void
  onAddFiles: (files: File[]) => void
  onRemoveImage: (localId: string) => void
  onRetryImage: (localId: string) => void
  onSubmit: () => void
  onCancel: () => void
  onAdvancedView: () => void
}

/**
 * Right column of P-06. Three collapsible sections (text / reference /
 * inpaint) gate their inputs behind a checkbox so the user can opt in
 * to each surface, but the actual submit-eligibility check operates on
 * input values, not the toggles — toggling a section without filling
 * it doesn't make the button light up. The Inpaint canvas itself lives
 * in the left column; this panel owns only the toggle + coverage badge
 * so the user always sees mask state next to the other inputs.
 */
export function AliasInputPanel(props: AliasInputPanelProps) {
  const {
    aliasName,
    freeformNote,
    textEnabled,
    referenceEnabled,
    inpaintEnabled,
    referenceItems,
    isUploading,
    maskCoveragePercent,
    isSubmitting,
    canSubmit,
    isCancelling,
    onAliasNameChange,
    onFreeformChange,
    onToggleText,
    onToggleReference,
    onToggleInpaint,
    onAddFiles,
    onRemoveImage,
    onRetryImage,
    onSubmit,
    onCancel,
    onAdvancedView,
  } = props

  const noteLength = freeformNote.length
  const overLimit = noteLength > FREEFORM_NOTE_MAX
  const aliasNameTrimmed = aliasName.trim()
  const aliasNameValid = aliasNameTrimmed.length > 0 && aliasNameTrimmed.length <= ALIAS_NAME_MAX
  const submitDisabled = isSubmitting || isUploading || !aliasNameValid || !canSubmit || overLimit
  const remaining = MAX_REFERENCE_IMAGES - referenceItems.length

  return (
    <aside className="flex w-full flex-col gap-4" aria-label="Alias 輸入">
      <div className="flex flex-col gap-1">
        <Label htmlFor="alias-name">Alias 名稱</Label>
        <Input
          id="alias-name"
          value={aliasName}
          onChange={(e) => onAliasNameChange(e.target.value)}
          maxLength={ALIAS_NAME_MAX}
          placeholder="例如：紅旗袍版"
          autoFocus
        />
        <p className="text-xs text-muted-foreground">最多 {ALIAS_NAME_MAX} 字</p>
      </div>

      <div className="flex flex-col gap-1 text-xs text-muted-foreground">
        輸入方式（至少選一種，可混用）
      </div>

      {/* ---- Section: text -------------------------------------------------- */}
      <section
        data-testid="section-text"
        className="rounded-md border border-border/60 p-3"
        aria-labelledby="section-text-label"
      >
        <label className="flex items-center gap-2 text-sm font-medium">
          <Checkbox
            checked={textEnabled}
            onCheckedChange={(checked) => onToggleText(checked === true)}
            data-testid="section-text-toggle"
            aria-labelledby="section-text-label"
          />
          <span id="section-text-label">文字補述</span>
        </label>
        {textEnabled && (
          <div className="mt-3 flex flex-col gap-1">
            <Label htmlFor="alias-freeform-note" className="sr-only">
              Alias 補述內容
            </Label>
            <Textarea
              id="alias-freeform-note"
              rows={3}
              maxLength={FREEFORM_NOTE_MAX}
              value={freeformNote}
              onChange={(e) => onFreeformChange(e.target.value)}
              placeholder="例：換成紅色旗袍、髮型保持不變"
              aria-invalid={overLimit ? 'true' : undefined}
            />
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>會自動翻譯為英文送至模型</span>
              <span data-testid="freeform-counter">
                {noteLength}/{FREEFORM_NOTE_MAX}
              </span>
            </div>
          </div>
        )}
      </section>

      {/* ---- Section: reference image -------------------------------------- */}
      <section
        data-testid="section-reference"
        className="rounded-md border border-border/60 p-3"
        aria-labelledby="section-reference-label"
      >
        <label className="flex items-center gap-2 text-sm font-medium">
          <Checkbox
            checked={referenceEnabled}
            onCheckedChange={(checked) => onToggleReference(checked === true)}
            data-testid="section-reference-toggle"
            aria-labelledby="section-reference-label"
          />
          <span id="section-reference-label">
            參考圖（{referenceItems.length}/{MAX_REFERENCE_IMAGES}）
          </span>
        </label>
        {referenceEnabled && (
          <div className="mt-3 flex flex-col gap-2">
            <ReferenceImageDropzone
              remaining={remaining}
              onFiles={onAddFiles}
              disabled={isSubmitting}
            />
            {referenceItems.length > 0 && (
              <div data-testid="alias-reference-grid" className="grid grid-cols-3 gap-2">
                {referenceItems.map((item) => (
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
        )}
      </section>

      {/* ---- Section: inpaint ---------------------------------------------- */}
      <section
        data-testid="section-inpaint"
        className="rounded-md border border-border/60 p-3"
        aria-labelledby="section-inpaint-label"
      >
        <label className="flex items-center gap-2 text-sm font-medium">
          <Checkbox
            checked={inpaintEnabled}
            onCheckedChange={(checked) => onToggleInpaint(checked === true)}
            data-testid="section-inpaint-toggle"
            aria-labelledby="section-inpaint-label"
          />
          <span id="section-inpaint-label">標記區域 (Inpaint)</span>
        </label>
        {inpaintEnabled && (
          <div className="mt-3 flex flex-col gap-2 text-xs text-muted-foreground">
            <p>左側 Base 圖會切換成繪製模式，用筆刷標出要重畫的區域。</p>
            <MaskPreviewBadge coveragePercent={maskCoveragePercent} />
          </div>
        )}
      </section>

      {/* ---- Actions ------------------------------------------------------- */}
      <div className="flex flex-col gap-2">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={onAdvancedView}
          className="self-start"
        >
          進階檢視 Prompt
        </Button>
        <Button
          type="button"
          onClick={onSubmit}
          disabled={submitDisabled}
          data-testid="alias-submit"
        >
          {isSubmitting && <Loader2 className="size-4 animate-spin" aria-hidden />}
          {isSubmitting ? '送出中…' : '生成 Alias'}
        </Button>
        <Button
          type="button"
          variant="ghost"
          onClick={onCancel}
          disabled={isCancelling}
          data-testid="alias-cancel"
        >
          取消
        </Button>
        {!canSubmit && aliasNameValid && (
          <p data-testid="alias-submit-hint" className="text-xs text-muted-foreground">
            至少要填一項：文字補述、參考圖、或 Inpaint mask。
          </p>
        )}
        {!aliasNameValid && (
          <p data-testid="alias-submit-hint" className="text-xs text-muted-foreground">
            請先填 Alias 名稱。
          </p>
        )}
      </div>
    </aside>
  )
}
