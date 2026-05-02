import { useEffect, useState, type FormEvent } from 'react'
import { Loader2 } from 'lucide-react'

import {
  createMotion,
  type CreateMotionResponse,
  type MotionParentRef,
} from '@/api/endpoints/motions'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { AgentError } from '@/lib/agentError'
import { toast } from '@/stores/toastStore'

const NAME_MAX = 50
const DESCRIPTION_MAX = 500

export interface CustomMotionModalProps {
  isOpen: boolean
  parent: MotionParentRef
  onClose: () => void
  /**
   * Fired after a successful POST. Caller is responsible for inserting
   * the new motion into MotionRow's pending custom map and subscribing
   * the SSE stream — this component only owns the form lifecycle.
   */
  onSuccess: (response: CreateMotionResponse, name: string, description: string) => void
}

/**
 * Modal M-02 (per UX wireframes §11). Lets the owner spawn a custom
 * motion: name + free-form Chinese description that the backend
 * reconciler later translates to English.
 */
export function CustomMotionModal({ isOpen, parent, onClose, onSuccess }: CustomMotionModalProps) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [isPending, setIsPending] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  // Reset on open so a previous failure / leftover input doesn't leak
  // into the next session.
  useEffect(() => {
    if (isOpen) {
      setName('')
      setDescription('')
      setIsPending(false)
      setErrorMessage(null)
    }
  }, [isOpen])

  const trimmedName = name.trim()
  const trimmedDescription = description.trim()
  const nameOver = name.length > NAME_MAX
  const descriptionOver = description.length > DESCRIPTION_MAX
  const canSubmit =
    !isPending &&
    trimmedName.length > 0 &&
    trimmedDescription.length > 0 &&
    !nameOver &&
    !descriptionOver

  const handleClose = () => {
    if (isPending) return
    onClose()
  }

  const handleSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    if (!canSubmit) return
    setIsPending(true)
    setErrorMessage(null)
    try {
      const response = await createMotion(parent, {
        motion_type: 'custom',
        name: trimmedName,
        description: trimmedDescription,
      })
      onSuccess(response, trimmedName, trimmedDescription)
    } catch (err) {
      const agent = AgentError.from(err)
      // VALIDATION_ / CONFLICT_ → inline so the user can fix the field
      // without losing what they typed. Everything else (MODEL_,
      // INTERNAL_, network) goes to the global toast layer.
      if (agent.isCategory('VALIDATION_') || agent.isCategory('CONFLICT_')) {
        setErrorMessage(
          agent.code === 'CONFLICT_DUPLICATE_NAME'
            ? '此 motion 名稱已被使用'
            : agent.message || '輸入無效',
        )
      } else {
        toast.agentError(agent)
        setErrorMessage(agent.message || '生成失敗')
      }
      setIsPending(false)
    }
  }

  return (
    <Dialog
      open={isOpen}
      onOpenChange={(open) => {
        if (!open) handleClose()
      }}
    >
      <DialogContent
        data-testid="custom-motion-modal"
        showCloseButton={!isPending}
        className="max-w-md"
      >
        <DialogHeader>
          <DialogTitle>新增自訂 Motion</DialogTitle>
          <DialogDescription>
            輸入動作名稱與描述，描述會在後端翻譯為英文送至 Veo。
          </DialogDescription>
        </DialogHeader>
        <form noValidate onSubmit={handleSubmit} className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="custom-motion-name">動作名稱 *</Label>
            <Input
              id="custom-motion-name"
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={isPending}
              aria-invalid={nameOver ? 'true' : undefined}
              aria-describedby="custom-motion-name-counter"
              data-testid="custom-motion-name-input"
            />
            <div
              id="custom-motion-name-counter"
              className={`text-xs ${nameOver ? 'text-destructive' : 'text-muted-foreground'}`}
              data-testid="custom-motion-name-counter"
            >
              {name.length}/{NAME_MAX}
            </div>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="custom-motion-description">動作描述 *</Label>
            <Textarea
              id="custom-motion-description"
              rows={4}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              disabled={isPending}
              aria-invalid={descriptionOver ? 'true' : undefined}
              aria-describedby="custom-motion-description-counter"
              data-testid="custom-motion-description-input"
            />
            <div
              id="custom-motion-description-counter"
              className={`text-xs ${descriptionOver ? 'text-destructive' : 'text-muted-foreground'}`}
              data-testid="custom-motion-description-counter"
            >
              {description.length}/{DESCRIPTION_MAX}
            </div>
          </div>
          {errorMessage ? (
            <p role="alert" className="text-xs text-destructive" data-testid="custom-motion-error">
              {errorMessage}
            </p>
          ) : null}
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={handleClose} disabled={isPending}>
              取消
            </Button>
            <Button type="submit" disabled={!canSubmit} data-testid="custom-motion-submit">
              {isPending ? (
                <>
                  <Loader2 className="size-3.5 animate-spin" aria-hidden />
                  生成中…
                </>
              ) : (
                '生成'
              )}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
