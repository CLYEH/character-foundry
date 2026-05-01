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

export interface AliasDeleteConfirmProps {
  isOpen: boolean
  aliasName: string
  isPending: boolean
  errorMessage: string | null
  onClose: () => void
  onConfirm: () => void
}

/**
 * Destructive confirm dialog for alias deletion (M-04).
 *
 * Same shape as SelectBaseConfirmDialog — we deliberately keep this
 * one-off rather than introducing a generic ConfirmDialog wrapper until
 * a third confirm surface arrives (per the comment in
 * SelectBaseConfirmDialog).
 */
export function AliasDeleteConfirm({
  isOpen,
  aliasName,
  isPending,
  errorMessage,
  onClose,
  onConfirm,
}: AliasDeleteConfirmProps) {
  return (
    <Dialog
      open={isOpen}
      onOpenChange={(open) => {
        if (!open && !isPending) onClose()
      }}
    >
      <DialogContent
        data-testid="alias-delete-confirm"
        showCloseButton={!isPending}
        className="max-w-md"
      >
        <DialogHeader>
          <DialogTitle>刪除 Alias「{aliasName}」？</DialogTitle>
          <DialogDescription>
            刪除後該 Alias 與其底下所有 motion 都會一併移除，30 天內可從垃圾桶還原。
          </DialogDescription>
        </DialogHeader>
        {errorMessage ? (
          <p role="alert" className="text-xs text-destructive" data-testid="alias-delete-error">
            {errorMessage}
          </p>
        ) : null}
        <DialogFooter>
          <Button type="button" variant="ghost" onClick={onClose} disabled={isPending}>
            取消
          </Button>
          <Button
            type="button"
            variant="destructive"
            onClick={onConfirm}
            disabled={isPending}
            data-testid="alias-delete-confirm-action"
          >
            {isPending ? (
              <>
                <Loader2 className="size-3.5 animate-spin" aria-hidden />
                刪除中…
              </>
            ) : (
              '確認刪除'
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
