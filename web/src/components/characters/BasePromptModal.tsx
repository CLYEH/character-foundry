import type { Base } from '@/api/endpoints/characters'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

export interface BasePromptModalProps {
  isOpen: boolean
  onClose: () => void
  base: Base
}

/**
 * Read-only "complete prompt" view for the Character Detail page.
 *
 * BaseDTO (api-shape §6.3) doesn't currently carry the prompt fields
 * (`menu_selections`, `freeform_note`) needed to round-trip through
 * `POST /v1/prompt/preview` like T-024's PromptPreviewModal does. Until
 * the backend extends the schema (STATUS.md backlog S2-6) we surface
 * what we *do* have — source checkpoint id + timestamp — and document
 * the gap inline so the user knows full prompt audit lives on a follow-
 * up. The ticket explicitly allows "inline 一段 read-only view" instead
 * of reusing T-024.
 */
export function BasePromptModal(props: BasePromptModalProps) {
  const { isOpen, onClose, base } = props
  return (
    <Dialog
      open={isOpen}
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
    >
      <DialogContent data-testid="base-prompt-modal" className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Base 完整資訊</DialogTitle>
          <DialogDescription>Base 一旦確立即不可修改，以下資訊呈現它的來源。</DialogDescription>
        </DialogHeader>
        <dl className="grid grid-cols-[8rem_1fr] gap-x-3 gap-y-2 text-sm">
          <dt className="text-muted-foreground">來源 Checkpoint</dt>
          <dd className="font-mono text-xs">{base.from_checkpoint_id}</dd>
          <dt className="text-muted-foreground">確立時間</dt>
          <dd>{new Date(base.created_at).toLocaleString()}</dd>
        </dl>
        <p className="rounded-md border border-amber-300/60 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-400/40 dark:bg-amber-950/50 dark:text-amber-100">
          完整 prompt 內容（平台 constraints、選單、補述、最終送模型字串）將於後續 ticket 補上 —
          目前 BaseDTO 尚未攜帶 prompt 欄位。
        </p>
      </DialogContent>
    </Dialog>
  )
}
