import { Loader2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

export interface SelectBaseConfirmDialogProps {
  isOpen: boolean
  onClose: () => void
  onConfirm: () => void
  isPending: boolean
  /** Sequence label for the chosen checkpoint (e.g. "#3"). Optional —
   *  server-loaded checkpoints might not have it. */
  sequenceLabel?: string | null
}

/**
 * Destructive confirmation before promoting a checkpoint to Base.
 *
 * The ticket calls for shadcn AlertDialog; we don't have that primitive
 * vendored yet and don't need its full role-alertdialog semantics for a
 * single binary decision. Standard Dialog with destructive button styling
 * keeps the new surface area small. If we end up needing more confirm
 * dialogs (delete character, abandon session) we can extract a primitive.
 */
export function SelectBaseConfirmDialog(props: SelectBaseConfirmDialogProps) {
  const { isOpen, onClose, onConfirm, isPending, sequenceLabel } = props

  return (
    <Dialog
      open={isOpen}
      onOpenChange={(open) => {
        if (!open && !isPending) onClose()
      }}
    >
      <DialogContent
        data-testid="select-base-confirm"
        showCloseButton={!isPending}
        className="max-w-md"
      >
        <DialogHeader>
          <DialogTitle>確立為 Base？</DialogTitle>
          <DialogDescription>
            確立後 Base 將不可修改，後續所有 Alias 都會以這張為基底。
            {sequenceLabel ? `將使用 Checkpoint ${sequenceLabel}。` : ''}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button type="button" variant="ghost" onClick={onClose} disabled={isPending}>
            取消
          </Button>
          <Button
            type="button"
            variant="destructive"
            onClick={onConfirm}
            disabled={isPending}
            data-testid="select-base-confirm-action"
          >
            {isPending ? (
              <>
                <Loader2 className="size-3.5 animate-spin" aria-hidden />
                確立中…
              </>
            ) : (
              '確立 Base'
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
