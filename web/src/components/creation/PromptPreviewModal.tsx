import { useCallback, useState } from 'react'
import { Copy, Loader2 } from 'lucide-react'

import { usePromptPreview } from '@/api/queries/usePromptPreview'
import type { PromptPreviewRequest, PromptPreviewResponse } from '@/api/endpoints/prompt'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { AgentError } from '@/lib/agentError'
import { toast } from '@/stores/toastStore'

export interface PromptPreviewModalProps {
  isOpen: boolean
  onClose: () => void
  request: PromptPreviewRequest
  /**
   * When set, the modal skips the API call and renders an inline notice
   * instead. Used when the *next* generation will diverge from what
   * `POST /v1/prompt/preview` can faithfully represent — currently remix
   * mode, where the worker reconciles with `has_reference_image=True`
   * (sourced from the parent checkpoint) but the preview endpoint has no
   * `base_checkpoint_id` field to mirror that signal. Better to be honest
   * about the gap than to render a misleading "audit" prompt.
   */
  unsupportedReason?: string | null
}

/**
 * M-01 Advanced Prompt modal. Shows the four prompt-assembly artefacts
 * returned by `POST /v1/prompt/preview` (platform constraints, menu
 * fragments, reconciled English note, final prompt) so the user can audit
 * what the model will actually receive without giving them a way to edit
 * it directly (per F-04b — only inputs are user-editable).
 *
 * The dialog is controlled. Radix unmounts `DialogContent` on close, which
 * means each open mounts a fresh `usePromptPreview`, which fires a fresh
 * request. That's the behaviour the ticket wants ("關閉 modal 後再開 → 重新呼
 * API"); the backend Redis cache absorbs duplicate work so this is cheap.
 */
export function PromptPreviewModal(props: PromptPreviewModalProps) {
  const { isOpen, onClose, request, unsupportedReason } = props

  return (
    <Dialog
      open={isOpen}
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
    >
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>進階檢視 Prompt</DialogTitle>
          <DialogDescription>
            送進 gpt-image-2 的最終 prompt。組合自平台 constraints、選單片段，與 LLM 重寫後的補述。
          </DialogDescription>
        </DialogHeader>
        {unsupportedReason ? (
          <UnsupportedNotice reason={unsupportedReason} />
        ) : (
          <PromptPreviewBody request={request} />
        )}
      </DialogContent>
    </Dialog>
  )
}

function UnsupportedNotice({ reason }: { reason: string }) {
  return (
    <div
      role="status"
      data-testid="prompt-preview-unsupported"
      className="rounded-md border border-amber-300/60 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-400/40 dark:bg-amber-950/50 dark:text-amber-100"
    >
      {reason}
    </div>
  )
}

function PromptPreviewBody({ request }: { request: PromptPreviewRequest }) {
  const query = usePromptPreview(request)

  if (query.isPending) {
    return (
      <div
        data-testid="prompt-preview-loading"
        className="flex items-center justify-center gap-2 py-12 text-sm text-muted-foreground"
      >
        <Loader2 className="size-4 animate-spin" aria-hidden />
        正在組合 prompt…
      </div>
    )
  }

  if (query.isError) {
    return <PromptPreviewError error={AgentError.from(query.error)} />
  }

  return <PromptPreviewSections data={query.data} />
}

function PromptPreviewError({ error }: { error: AgentError }) {
  // Per T-024 acceptance:
  //   - VALIDATION_EMPTY_INPUT → friendly hint (caller forgot to fill form)
  //   - PROMPT_CONFLICT        → render AgentError detail (message + problem + fix)
  // Other codes still get the AgentError surface so unknown failures are
  // legible rather than silently blank. This deliberately overrides the
  // global `mapAgentErrorToUI` mapping (which sends PROMPT_* to a toast) —
  // the modal is the right surface here because the user opened it
  // explicitly to inspect prompt assembly.
  if (error.code === 'VALIDATION_EMPTY_INPUT') {
    return (
      <div
        role="alert"
        data-testid="prompt-preview-error-empty"
        className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive"
      >
        請先填選項或補述
      </div>
    )
  }

  return (
    <div
      role="alert"
      data-testid="prompt-preview-error"
      className="flex flex-col gap-2 rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive"
    >
      <div className="font-medium">{error.message}</div>
      {error.problem && (
        <div className="text-xs">
          <span className="font-medium">Problem：</span>
          {error.problem}
        </div>
      )}
      {error.fix && (
        <div className="text-xs">
          <span className="font-medium">Fix：</span>
          {error.fix}
        </div>
      )}
    </div>
  )
}

function PromptPreviewSections({ data }: { data: PromptPreviewResponse }) {
  return (
    <div className="flex flex-col gap-4">
      <Section title="平台固定 constraints">
        <pre className="whitespace-pre-wrap font-mono text-xs">{data.platform_constraints}</pre>
      </Section>

      <Section title="選單片段">
        {data.menu_fragments.length === 0 ? (
          <p className="text-xs text-muted-foreground">（未指定任何選單）</p>
        ) : (
          <ul
            data-testid="prompt-preview-menu-fragments"
            className="list-disc pl-5 font-mono text-xs"
          >
            {data.menu_fragments.map((fragment, idx) => (
              <li key={idx}>{fragment}</li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="重寫後的補述（英文）">
        {data.reconciled_note_en ? (
          <pre className="whitespace-pre-wrap font-mono text-xs">{data.reconciled_note_en}</pre>
        ) : (
          <p className="text-xs text-muted-foreground">（無補述）</p>
        )}
      </Section>

      <Section title="最終 prompt" action={<CopyButton text={data.final_prompt} />}>
        <pre
          data-testid="prompt-preview-final"
          className="max-h-64 overflow-auto whitespace-pre-wrap rounded-md bg-muted px-3 py-2 font-mono text-xs"
        >
          {data.final_prompt}
        </pre>
      </Section>
    </div>
  )
}

function Section({
  title,
  action,
  children,
}: {
  title: string
  action?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <section className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium">{title}</h3>
        {action}
      </div>
      {children}
    </section>
  )
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      toast.success('已複製')
      window.setTimeout(() => setCopied(false), 2000)
    } catch {
      // Modern browsers under a secure context expose `navigator.clipboard`.
      // The ticket explicitly opts out of a fallback, so we just surface the
      // failure to the user.
      toast.error('複製失敗')
    }
  }, [text])

  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      onClick={handleCopy}
      data-testid="prompt-preview-copy"
    >
      <Copy className="size-3.5" aria-hidden />
      {copied ? '已複製' : '複製'}
    </Button>
  )
}
