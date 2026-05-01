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

export interface MotionDeleteConfirmProps {
  isOpen: boolean
  motionName: string
  isPending: boolean
  errorMessage: string | null
  onClose: () => void
  onConfirm: () => void
}

/**
 * Destructive confirm dialog for motion deletion. Mirrors
 * `AliasDeleteConfirm` — same shape and behaviour kept deliberately
 * separate until a third confirm surface arrives (per the comment in
 * SelectBaseConfirmDialog about deferring a generic ConfirmDialog).
 */
export function MotionDeleteConfirm({
  isOpen,
  motionName,
  isPending,
  errorMessage,
  onClose,
  onConfirm,
}: MotionDeleteConfirmProps) {
  return (
    <Dialog
      open={isOpen}
      onOpenChange={(open) => {
        if (!open && !isPending) onClose()
      }}
    >
      <DialogContent
        data-testid="motion-delete-confirm"
        showCloseButton={!isPending}
        className="max-w-md"
      >
        <DialogHeader>
          <DialogTitle>刪除 Motion「{motionName}」？</DialogTitle>
          <DialogDescription>
            刪除後該 motion 將從清單移除，30 天內可從垃圾桶還原。
          </DialogDescription>
        </DialogHeader>
        {errorMessage ? (
          <p role="alert" className="text-xs text-destructive" data-testid="motion-delete-error">
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
            data-testid="motion-delete-confirm-action"
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
