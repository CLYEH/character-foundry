import { useCallback, useState } from 'react'
import { Copy, Loader2 } from 'lucide-react'

import { usePromptPreview } from '@/api/queries/usePromptPreview'
import type {
  AliasInputMode,
  PromptPreviewAliasRequest,
  PromptPreviewMotionRequest,
  PromptPreviewRequest,
  PromptPreviewResponse,
} from '@/api/endpoints/prompt'
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
}

const ALIAS_INPUT_MODE_LABEL: Record<AliasInputMode, string> = {
  text: '文字補述',
  image: '參考圖',
  inpaint: 'Inpaint 局部編輯',
  mixed: '文字 + 參考圖',
}

/**
 * M-01 Advanced Prompt modal. Shows the prompt-assembly artefacts returned
 * by `POST /v1/prompt/preview` so the user can audit what the model will
 * actually receive without giving them a way to edit it directly (per
 * F-04b — only inputs are user-editable).
 *
 * Three modes (T-035 backend / T-040 frontend):
 *   - `create_base`   — Creation Session
 *   - `create_alias`  — Alias edit page (T-036), shows derived_from base thumbnail
 *   - `create_motion` — Custom Motion modal (T-039), shows parent thumbnail;
 *                       preset templates skip the reconciler block
 *
 * The dialog is controlled. Radix unmounts `DialogContent` on close, which
 * means each open mounts a fresh `usePromptPreview`, which fires a fresh
 * request. That's the behaviour T-024 wants ("關閉 modal 後再開 → 重新呼
 * API"); the backend Redis cache absorbs duplicate work.
 */
export function PromptPreviewModal(props: PromptPreviewModalProps) {
  const { isOpen, onClose, request } = props

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
          <DialogDescription>{describeMode(request)}</DialogDescription>
        </DialogHeader>
        <PromptPreviewBody request={request} />
      </DialogContent>
    </Dialog>
  )
}

function describeMode(request: PromptPreviewRequest): string {
  switch (request.mode) {
    case 'create_base':
      return '送進 gpt-image-2 的最終 prompt。組合自平台 constraints、選單片段，與 LLM 重寫後的補述。'
    case 'create_alias':
      return '在既有 Base 上做 alias 變體；以下顯示送進 gpt-image-2 的最終 prompt 與來源 Base。'
    case 'create_motion':
      return '從 Base 或 Alias 生 i2v 動作；以下顯示送進 Veo 3.1 的最終 prompt 與 parent 圖。'
  }
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

  return <PromptPreviewSections request={request} data={query.data} />
}

function PromptPreviewError({ error }: { error: AgentError }) {
  // Per T-024 acceptance:
  //   - VALIDATION_EMPTY_INPUT → friendly hint (caller forgot to fill form)
  //   - VALIDATION_MASK_REQUIRED → friendly hint (alias inpaint with empty mask)
  //   - PROMPT_CONFLICT → render AgentError detail (message + problem + fix)
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

  if (error.code === 'VALIDATION_MASK_REQUIRED') {
    return (
      <div
        role="alert"
        data-testid="prompt-preview-error-mask"
        className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive"
      >
        請先在畫布上圈選要編輯的區域
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

function PromptPreviewSections({
  request,
  data,
}: {
  request: PromptPreviewRequest
  data: PromptPreviewResponse
}) {
  return (
    <div className="flex flex-col gap-4">
      {request.mode === 'create_alias' && <AliasContextHeader request={request} data={data} />}
      {request.mode === 'create_motion' && <MotionContextHeader request={request} data={data} />}

      <Section title="平台固定 constraints">
        <pre className="whitespace-pre-wrap font-mono text-xs">{data.platform_constraints}</pre>
      </Section>

      {shouldShowReconcilerBlock(request, data) && (
        <>
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
              <pre
                data-testid="prompt-preview-reconciled-note"
                className="whitespace-pre-wrap font-mono text-xs"
              >
                {data.reconciled_note_en}
              </pre>
            ) : (
              <p className="text-xs text-muted-foreground">（無補述）</p>
            )}
          </Section>
        </>
      )}

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

/**
 * Preset motions inline a fixed platform template (T-033 yaml) without a
 * reconciler call, so the menu-fragments and reconciled-note blocks would
 * just be empty noise — skip them. Custom motions and the two image modes
 * always go through the reconciler.
 *
 * `'custom_reconciled'` is the only positive signal for "reconciler ran";
 * anything else (preset literal, missing field on a contract drift) falls
 * back to the quieter preset render rather than showing two empty sections.
 */
function shouldShowReconcilerBlock(
  request: PromptPreviewRequest,
  data: PromptPreviewResponse,
): boolean {
  if (request.mode !== 'create_motion') return true
  return data.motion_template_used === 'custom_reconciled'
}

function AliasContextHeader({
  request,
  data,
}: {
  request: PromptPreviewAliasRequest
  data: PromptPreviewResponse
}) {
  const note = (request.freeform_note ?? '').trim()
  const refCount = request.reference_image_ids?.length ?? 0
  const hasMask = request.mask != null
  return (
    <ContextHeader testId="prompt-preview-alias-context" title="來源 Base">
      <Thumbnail
        src={data.derived_from?.base_image_url ?? null}
        alt="來源 Base 縮圖"
        testId="prompt-preview-alias-thumb"
      />
      <dl className="flex-1 grid grid-cols-[6rem_1fr] gap-x-3 gap-y-1 text-xs">
        <dt className="text-muted-foreground">輸入模式</dt>
        <dd data-testid="prompt-preview-alias-input-mode">
          {ALIAS_INPUT_MODE_LABEL[request.input_mode]}
        </dd>
        {note.length > 0 && (
          <>
            <dt className="text-muted-foreground">補述</dt>
            <dd className="whitespace-pre-wrap">{note}</dd>
          </>
        )}
        {refCount > 0 && (
          <>
            <dt className="text-muted-foreground">參考圖</dt>
            <dd>{refCount} 張</dd>
          </>
        )}
        {hasMask && (
          <>
            <dt className="text-muted-foreground">Inpaint mask</dt>
            <dd data-testid="prompt-preview-alias-mask">已圈選</dd>
          </>
        )}
      </dl>
    </ContextHeader>
  )
}

function MotionContextHeader({
  request,
  data,
}: {
  request: PromptPreviewMotionRequest
  data: PromptPreviewResponse
}) {
  const description = (request.description ?? '').trim()
  // Mirror shouldShowReconcilerBlock — `'custom_reconciled'` is the only
  // positive signal for "custom"; everything else (preset literal, missing
  // field) renders as preset.
  const usedPreset = data.motion_template_used !== 'custom_reconciled'
  return (
    <ContextHeader testId="prompt-preview-motion-context" title="Parent">
      <Thumbnail
        src={data.parent?.image_url ?? null}
        alt="Parent 縮圖"
        testId="prompt-preview-motion-thumb"
      />
      <dl className="flex-1 grid grid-cols-[6rem_1fr] gap-x-3 gap-y-1 text-xs">
        <dt className="text-muted-foreground">Parent 類型</dt>
        <dd>{request.parent_type === 'base' ? 'Base' : 'Alias'}</dd>
        <dt className="text-muted-foreground">Motion</dt>
        <dd data-testid="prompt-preview-motion-type">
          {request.motion_type}
          {usedPreset && (
            <span
              data-testid="prompt-preview-motion-preset-badge"
              className="ml-2 rounded-sm bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground"
            >
              使用平台預設模板
            </span>
          )}
        </dd>
        {description.length > 0 && (
          <>
            <dt className="text-muted-foreground">描述</dt>
            <dd className="whitespace-pre-wrap">{description}</dd>
          </>
        )}
      </dl>
    </ContextHeader>
  )
}

function ContextHeader({
  testId,
  title,
  children,
}: {
  testId: string
  title: string
  children: React.ReactNode
}) {
  return (
    <section
      data-testid={testId}
      className="flex gap-3 rounded-md border border-border/60 bg-muted/40 p-3"
    >
      <div className="flex flex-col gap-1.5">
        <h3 className="text-xs font-medium text-muted-foreground">{title}</h3>
      </div>
      {children}
    </section>
  )
}

function Thumbnail({ src, alt, testId }: { src: string | null; alt: string; testId: string }) {
  return (
    <div
      data-testid={testId}
      className="relative size-20 shrink-0 overflow-hidden rounded-md border border-border/60 bg-background"
    >
      {src ? (
        <img src={src} alt={alt} className="absolute inset-0 size-full object-cover" />
      ) : (
        <div className="absolute inset-0 flex items-center justify-center text-[10px] text-muted-foreground">
          無圖
        </div>
      )}
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
