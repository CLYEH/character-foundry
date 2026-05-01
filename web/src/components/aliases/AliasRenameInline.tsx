import { type FormEvent, useEffect, useState } from 'react'
import { Loader2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

export interface AliasRenameInlineProps {
  aliasId: string
  initialName: string
  isPending: boolean
  errorMessage: string | null
  onSubmit: (newName: string) => void
  onCancel: () => void
}

/**
 * Inline rename form for an Alias. Mirrors the slim "Input + 確定 / 取消"
 * pattern used by the SelectBaseConfirmDialog footer; we don't need the
 * full FormField wrapper here because there's no label / hint text and
 * the only validation that matters (duplicate name, invalid chars) comes
 * from the backend via `errorMessage`.
 */
export function AliasRenameInline({
  aliasId,
  initialName,
  isPending,
  errorMessage,
  onSubmit,
  onCancel,
}: AliasRenameInlineProps) {
  const [value, setValue] = useState(initialName)

  // Reset to the latest server-confirmed name when the user re-opens the
  // form for the same alias (or switches to a different one).
  useEffect(() => {
    setValue(initialName)
  }, [aliasId, initialName])

  const trimmed = value.trim()
  const canSubmit = !isPending && trimmed.length > 0 && trimmed !== initialName

  const handleSubmit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    if (!canSubmit) return
    onSubmit(trimmed)
  }

  return (
    <form
      data-testid={`alias-rename-form-${aliasId}`}
      className="flex flex-col gap-1"
      onSubmit={handleSubmit}
    >
      <div className="flex flex-wrap items-center gap-2">
        <Input
          autoFocus
          value={value}
          onChange={(e) => setValue(e.target.value)}
          disabled={isPending}
          aria-label="Alias 名稱"
          data-testid={`alias-rename-input-${aliasId}`}
          className="h-8 w-48"
        />
        <Button
          type="submit"
          size="sm"
          disabled={!canSubmit}
          data-testid={`alias-rename-submit-${aliasId}`}
        >
          {isPending ? (
            <>
              <Loader2 className="size-3.5 animate-spin" aria-hidden />
              儲存中…
            </>
          ) : (
            '儲存'
          )}
        </Button>
        <Button type="button" variant="ghost" size="sm" disabled={isPending} onClick={onCancel}>
          取消
        </Button>
      </div>
      {errorMessage ? (
        <p
          role="alert"
          data-testid={`alias-rename-error-${aliasId}`}
          className="text-xs text-destructive"
        >
          {errorMessage}
        </p>
      ) : null}
    </form>
  )
}
