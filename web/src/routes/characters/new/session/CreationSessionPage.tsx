import { useCallback, useMemo, useRef, useState } from 'react'
import { ArrowLeft } from 'lucide-react'
import { Link, useParams } from 'react-router'
import { useQueryClient } from '@tanstack/react-query'

import { creationSessionQueryKey, useCreationSession } from '@/api/queries/useCreationSession'
import { useCreateCheckpoint } from '@/api/mutations/useCreateCheckpoint'
import { useCancelTask } from '@/api/mutations/useCancelTask'
import type {
  Checkpoint,
  CreateCheckpointRequest,
  CreationSessionDetail,
} from '@/api/endpoints/checkpoints'
import type { TaskEvent } from '@/api/endpoints/tasks'
import {
  CheckpointLightbox,
  CheckpointList,
  PromptPreviewModal,
  ReferenceInputPanel,
  TemplateInputPanel,
  type CheckpointCardModel,
  type RemixContext,
} from '@/components/creation'
import type { PromptPreviewRequest } from '@/api/endpoints/prompt'
import { GenericErrorPage } from '@/components/composite/ErrorPage'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { FREEFORM_MAX_LENGTH, type MenuKey, type MenuSelections } from '@/constants/menu_options'
import { useReferenceUpload } from '@/hooks/useReferenceUpload'
import { useTaskStream } from '@/hooks/useTaskStream'
import { AgentError, type AgentErrorPayload } from '@/lib/agentError'
import { toast } from '@/stores/toastStore'

interface PlaceholderState {
  taskId: string
  request: CreateCheckpointRequest
  status: CheckpointCardModel['status']
  sequence: number | null
  cancelRequested: boolean
  checkpoint: Checkpoint | null
  /**
   * Mirrors `event.error` for the lifetime of the placeholder. Required for
   * cancel-mutation synthetic events (`too_late_failed`) that never enter
   * the SSE `events` map (Codex P2 round 3 on PR #30).
   */
  error: AgentErrorPayload | null
  insertionIndex: number
}

const EMPTY_SELECTIONS: MenuSelections = Object.freeze({}) as MenuSelections

export default function CreationSessionPage() {
  const { id: sessionId } = useParams<{ id: string }>()
  const sessionQuery = useCreationSession(sessionId)
  const createCheckpoint = useCreateCheckpoint(sessionId ?? '')
  const cancelTaskMutation = useCancelTask()
  const queryClient = useQueryClient()

  const [menuSelections, setMenuSelections] = useState<MenuSelections>(EMPTY_SELECTIONS)
  const [freeformNote, setFreeformNote] = useState<string>('')
  const [remixContext, setRemixContext] = useState<RemixContext>(null)
  const [placeholders, setPlaceholders] = useState<Map<string, PlaceholderState>>(() => new Map())
  const [lightboxCheckpointId, setLightboxCheckpointId] = useState<string | null>(null)
  const [promptPreviewOpen, setPromptPreviewOpen] = useState(false)
  const referenceUpload = useReferenceUpload(sessionId ?? '')

  // Monotonic counter so a placeholder's render slot is stable even if state
  // updates batch out of order — Map insertion order alone isn't enough once
  // the same key is replaced mid-stream.
  const insertionCounterRef = useRef(0)

  const handleTerminal = useCallback(
    (taskId: string, event: TaskEvent) => {
      // The backend only writes `event.result.checkpoint` on `completed`; on
      // `failed` / `cancelled` we keep the placeholder image but flip status
      // so the card can render the error / strikethrough state. Terminal
      // status also clears the optimistic `cancelRequested` flag — the
      // task has settled, so the "取消中…" badge no longer applies.
      setPlaceholders((prev) => {
        const next = new Map(prev)
        for (const [checkpointId, placeholder] of next) {
          if (placeholder.taskId !== taskId) continue
          const finalCheckpoint = event.result?.checkpoint ?? null
          next.set(checkpointId, {
            ...placeholder,
            status: event.status === 'completed' ? 'completed' : event.status,
            sequence: finalCheckpoint?.sequence ?? placeholder.sequence,
            checkpoint: finalCheckpoint ?? placeholder.checkpoint,
            cancelRequested: false,
            error: event.error ?? placeholder.error,
          })
          break
        }
        return next
      })
      // Invalidate the server snapshot when a checkpoint lands so a later
      // remount / focus refetch lines up with what we just rendered locally
      // (async-patterns.md §2.3 / §9.1).
      if (event.status === 'completed' && sessionId) {
        void queryClient.invalidateQueries({ queryKey: creationSessionQueryKey(sessionId) })
      }
      // Failed events don't go through TanStack Query's mutation cache, so
      // wire them to the same Layer-2 toast (`async-patterns.md` §7.2) that
      // mutation failures use — gives the user the full problem/cause/fix.
      if (event.status === 'failed' && event.error) {
        toast.agentError(new AgentError(event.error))
      }
    },
    [queryClient, sessionId],
  )

  const { events, subscribe } = useTaskStream({ onTerminal: handleTerminal })

  // Build the merged card list: server-loaded checkpoints first (sequence
  // ascending), then placeholders/in-flight cards in their submit order.
  // Server checkpoints whose `id` matches a placeholder collapse to the
  // placeholder entry — that handles the race where the SSE result arrives
  // before refetching the list.
  const models: CheckpointCardModel[] = useMemo(() => {
    const result = sessionQuery.data
    return buildCardModels(result, placeholders, events)
  }, [sessionQuery.data, placeholders, events])

  const lightboxCheckpoint = useMemo<Checkpoint | null>(() => {
    if (!lightboxCheckpointId) return null
    const model = models.find((m) => m.checkpointId === lightboxCheckpointId)
    return model?.checkpoint ?? null
  }, [lightboxCheckpointId, models])

  // Panel-level [重試] uses `retry_same`, which the backend re-derives from a
  // server-side prompt record. That record only exists for completed
  // checkpoints — disable the button until one lands.
  const hasCompletedCheckpoint = useMemo(
    () => models.some((m) => m.status === 'completed'),
    [models],
  )
  const isSubmitting = createCheckpoint.isPending

  // ---- Mutations ----------------------------------------------------------

  // submit dispatches a fully-built request — `buildRequestFromForm` snapshots
  // the live form state once at click time, so retry paths that need the
  // *original* failed input (Codex P1 on PR #30) can pass it directly without
  // having to silence the form snapshot inside submit.
  const submit = useCallback(
    (request: CreateCheckpointRequest) => {
      if (!sessionId) return
      createCheckpoint.mutate(request, {
        onSuccess: ({ task_id, checkpoint_id }) => {
          insertionCounterRef.current += 1
          const insertionIndex = insertionCounterRef.current
          setPlaceholders((prev) => {
            const next = new Map(prev)
            next.set(checkpoint_id, {
              taskId: task_id,
              request,
              status: 'queued',
              sequence: null,
              cancelRequested: false,
              checkpoint: null,
              error: null,
              insertionIndex,
            })
            return next
          })
          subscribe(task_id)
        },
        onError: (err) => {
          // Form-level failures (VALIDATION_*, CONFLICT_*) bubble through the
          // global mutationCache toast — checkpoint creation has no inline
          // surface for them, so a toast is the right layer.
          const agent = AgentError.from(err)
          if (agent.isCategory('VALIDATION_') || agent.isCategory('CONFLICT_')) {
            toast.error(agent.message)
          }
        },
      })
    },
    [createCheckpoint, sessionId, subscribe],
  )

  const inputMode = sessionQuery.data?.session.input_mode ?? null
  const referenceImageIds = referenceUpload.referenceImageIds

  const buildRequestFromForm = useCallback(
    (
      mode: CreateCheckpointRequest['mode'],
      baseCheckpointId: string | null,
    ): CreateCheckpointRequest => {
      const trimmedNote = freeformNote.trim()
      // In reference mode the menu is hidden, so we never carry menu
      // selections — keeping this branch tight prevents a stale state
      // from a future template→reference toggle ever leaking into the
      // reference payload.
      const isReference = inputMode === 'reference'
      return {
        mode,
        base_checkpoint_id: baseCheckpointId,
        menu_selections: !isReference && hasAnyMenuValue(menuSelections) ? menuSelections : null,
        freeform_note: trimmedNote.length > 0 ? trimmedNote : null,
        reference_image_ids: isReference && referenceImageIds.length > 0 ? referenceImageIds : null,
      }
    },
    [freeformNote, inputMode, menuSelections, referenceImageIds],
  )

  const handleGenerate = useCallback(() => {
    submit(
      buildRequestFromForm(
        remixContext ? 'remix' : 'fresh',
        remixContext?.baseCheckpointId ?? null,
      ),
    )
  }, [buildRequestFromForm, remixContext, submit])

  const handleRetrySamePrompt = useCallback(() => {
    // [重試] uses retry_same when there's a checkpoint to anchor on; the
    // backend re-derives prompt server-side so menu/freeform are ignored.
    const newest = pickRetrySource(models)
    if (!newest) return
    submit(buildRequestFromForm('retry_same', newest.checkpointId))
  }, [buildRequestFromForm, models, submit])

  // Aliasing the stable callback off the hook return so `useCallback`'s
  // dep-array can reference a stable identifier — the eslint rule
  // doesn't recognise `referenceUpload.reset` as stable.
  const resetReferences = referenceUpload.reset
  const handleReset = useCallback(() => {
    setMenuSelections(EMPTY_SELECTIONS)
    setFreeformNote('')
    setRemixContext(null)
    resetReferences()
  }, [resetReferences])

  const handleAdvancedView = useCallback(() => {
    setPromptPreviewOpen(true)
  }, [])

  const promptPreviewRequest = useMemo<PromptPreviewRequest>(() => {
    const trimmedNote = freeformNote.trim()
    const isReference = inputMode === 'reference'
    return {
      mode: 'create_base',
      menu_selections: !isReference && hasAnyMenuValue(menuSelections) ? menuSelections : null,
      freeform_note: trimmedNote.length > 0 ? trimmedNote : null,
      reference_image_ids: isReference && referenceImageIds.length > 0 ? referenceImageIds : null,
    }
  }, [freeformNote, inputMode, menuSelections, referenceImageIds])

  const handleRemix = useCallback(
    (checkpointId: string, sequence: number | null) => {
      const placeholder = placeholders.get(checkpointId)
      if (placeholder?.request) {
        // We have the original inputs — prefill so the user can nudge them.
        setMenuSelections(
          (placeholder.request.menu_selections as MenuSelections | null) ?? EMPTY_SELECTIONS,
        )
        setFreeformNote(placeholder.request.freeform_note ?? '')
      }
      // Server-loaded checkpoints don't carry their original inputs in the
      // DTO (api-shape §6.7). We still flip into remix mode but leave the
      // form alone — the user can type fresh tweaks. F→B follow-up tracked
      // in STATUS.md backlog.
      setRemixContext({ baseCheckpointId: checkpointId, baseSequence: sequence })
    },
    [placeholders],
  )

  const handleSelectAsBase = useCallback(() => {
    // T-025 lands the actual select-base flow + redirect to /characters/:id.
    // We surface the action so the smoke test can assert the button exists,
    // but route the click to a toast until T-025 wires it up.
    toast.info('選作 Base 即將上線（T-025）')
  }, [])

  const handleRetryFailed = useCallback(
    (checkpointId: string) => {
      const placeholder = placeholders.get(checkpointId)
      if (!placeholder?.request) return
      // Replay the *original* request, not the current form state — failed
      // retries must be deterministic regardless of edits to the panel
      // since the failure (Codex P1 on PR #30).
      submit(placeholder.request)
    },
    [placeholders, submit],
  )

  const handleCancel = useCallback(
    (taskId: string) => {
      // Optimistic flip so the button shows "取消中…" without waiting on the
      // round-trip; the SSE final status (cancelled / completed / failed)
      // settles the card.
      setPlaceholders((prev) => {
        const next = new Map(prev)
        for (const [id, placeholder] of next) {
          if (placeholder.taskId === taskId) {
            next.set(id, { ...placeholder, cancelRequested: true })
            break
          }
        }
        return next
      })
      cancelTaskMutation.mutate(taskId, {
        onSuccess: ({ cancel_outcome, task }) => {
          // Route every outcome through `handleTerminal` with a synthetic
          // event so the placeholder picks up the task's `result.checkpoint`
          // (or `error`) rather than just flipping a status flag. Without
          // this, `too_late_completed` would settle to status='completed'
          // but with `checkpoint=null` / `sequence=null` and the card stays
          // visually stuck (Codex P1 on PR #30).
          switch (cancel_outcome) {
            case 'cancelled_immediately':
              // Backend already removed the task from the queue and may not
              // emit a trailing SSE (api-shape §5.5: "立即顯示「已取消」").
              handleTerminal(taskId, { status: 'cancelled' })
              toast.success('已取消')
              break
            case 'cancel_pending':
              // Server is still trying to abort mid-run; SSE will deliver
              // the final status and `handleTerminal` will settle the card.
              toast.info('取消中…')
              break
            case 'too_late_completed':
              handleTerminal(taskId, {
                status: 'completed',
                result: (task.result as { checkpoint?: Checkpoint } | null) ?? null,
              })
              toast.warning('來不及取消')
              break
            case 'too_late_failed':
              handleTerminal(taskId, {
                status: 'failed',
                error: task.error ?? null,
              })
              toast.warning('來不及取消')
              break
          }
        },
        onError: () => {
          // Roll back the optimistic flag so the user can try again.
          setPlaceholders((prev) => {
            const next = new Map(prev)
            for (const [id, placeholder] of next) {
              if (placeholder.taskId === taskId) {
                next.set(id, { ...placeholder, cancelRequested: false })
                break
              }
            }
            return next
          })
        },
      })
    },
    [cancelTaskMutation, handleTerminal],
  )

  const handleMenuChange = useCallback((key: MenuKey, value: string) => {
    setMenuSelections((prev) => ({ ...prev, [key]: value }))
  }, [])

  const handleFreeformChange = useCallback((value: string) => {
    // The textarea also enforces `maxLength` at the DOM layer; this just
    // catches the paste-with-prevent-default edge case.
    setFreeformNote(value.slice(0, FREEFORM_MAX_LENGTH))
  }, [])

  // Re-subscribing to in-flight checkpoints loaded from a fresh GET is a
  // T-027 concern (the DTO doesn't carry task_ids today). Documented for
  // future readers; no effect needed now.

  // ---- Render -------------------------------------------------------------

  if (!sessionId) {
    return <GenericErrorPage description="Session id 缺漏" />
  }

  if (sessionQuery.isPending) {
    return (
      <section className="flex flex-col gap-4">
        <Skeleton className="h-8 w-64" />
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[20rem_1fr]">
          <Skeleton className="h-96 w-full" />
          <Skeleton className="h-96 w-full" />
        </div>
      </section>
    )
  }

  if (sessionQuery.isError) {
    const agent = AgentError.from(sessionQuery.error)
    return (
      <GenericErrorPage
        description={agent.message || '無法載入 Creation Session'}
        onRetry={() => {
          void sessionQuery.refetch()
        }}
      />
    )
  }

  return (
    <section className="flex flex-col gap-6">
      <div>
        <Button asChild variant="ghost" size="sm" className="-ml-2">
          <Link to="/">
            <ArrowLeft className="size-4" aria-hidden />回 Dashboard
          </Link>
        </Button>
      </div>

      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold">建立角色</h1>
        <p className="text-sm text-muted-foreground">設定條件，按生成迭代候選圖。</p>
      </header>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[20rem_1fr]">
        {inputMode === 'reference' ? (
          <ReferenceInputPanel
            items={referenceUpload.items}
            freeformNote={freeformNote}
            remixSequence={remixContext?.baseSequence ?? null}
            hasAnyCheckpoint={hasCompletedCheckpoint}
            isSubmitting={isSubmitting}
            isUploading={referenceUpload.isUploading}
            hasReferenceReady={referenceImageIds.length > 0}
            onAddFiles={(files) => {
              void referenceUpload.addFiles(files)
            }}
            onRemoveImage={referenceUpload.remove}
            onRetryImage={referenceUpload.retry}
            onFreeformChange={handleFreeformChange}
            onGenerate={handleGenerate}
            onRetry={handleRetrySamePrompt}
            onReset={handleReset}
            onAdvancedView={handleAdvancedView}
          />
        ) : (
          <TemplateInputPanel
            menuSelections={menuSelections}
            freeformNote={freeformNote}
            remixSequence={remixContext?.baseSequence ?? null}
            hasAnyCheckpoint={hasCompletedCheckpoint}
            isSubmitting={isSubmitting}
            onMenuChange={handleMenuChange}
            onFreeformChange={handleFreeformChange}
            onGenerate={handleGenerate}
            onRetry={handleRetrySamePrompt}
            onReset={handleReset}
            onAdvancedView={handleAdvancedView}
          />
        )}
        <CheckpointList
          models={models}
          onCancel={handleCancel}
          onRetry={handleRetryFailed}
          onRemix={handleRemix}
          onSelectAsBase={handleSelectAsBase}
          onOpenLightbox={(id) => setLightboxCheckpointId(id)}
        />
      </div>

      <CheckpointLightbox
        checkpoint={lightboxCheckpoint}
        onClose={() => setLightboxCheckpointId(null)}
      />

      <PromptPreviewModal
        isOpen={promptPreviewOpen}
        onClose={() => setPromptPreviewOpen(false)}
        request={promptPreviewRequest}
      />
    </section>
  )
}

// ---- Helpers ------------------------------------------------------------

function hasAnyMenuValue(selections: MenuSelections): boolean {
  return Object.values(selections).some((v) => typeof v === 'string' && v.length > 0)
}

const TERMINAL_CARD_STATUSES = new Set<CheckpointCardModel['status']>([
  'completed',
  'failed',
  'cancelled',
])

function isTerminalStatus(status: CheckpointCardModel['status']): boolean {
  return TERMINAL_CARD_STATUSES.has(status)
}

function pickRetrySource(models: CheckpointCardModel[]): CheckpointCardModel | null {
  // Newest *completed* card is the safest anchor for retry_same; failed
  // checkpoints don't have a server-side prompt record yet.
  for (let i = models.length - 1; i >= 0; i -= 1) {
    if (models[i].status === 'completed') return models[i]
  }
  return null
}

function buildCardModels(
  detail: CreationSessionDetail | undefined,
  placeholders: Map<string, PlaceholderState>,
  events: ReadonlyMap<string, TaskEvent>,
): CheckpointCardModel[] {
  const byId = new Map<string, CheckpointCardModel>()

  // 1. Server-known checkpoints.
  for (const checkpoint of detail?.checkpoints ?? []) {
    byId.set(checkpoint.id, {
      checkpointId: checkpoint.id,
      sequence: checkpoint.sequence,
      status: 'completed',
      event: null,
      checkpoint,
      error: null,
      request: null,
      taskId: null,
      cancelRequested: false,
    })
  }

  // 2. Local placeholders — newer truth than the server snapshot for status,
  //    BUT for the actual checkpoint payload (image URLs, sequence) prefer
  //    server data when available so a refetched signed URL or a partial
  //    SSE result is replaced by the full GET response (Codex P2 on PR #30).
  //    Status precedence:
  //      a. Terminal `placeholder.status` (set by `handleTerminal` from SSE
  //         result or cancel outcomes) is the final word — never let a stale
  //         `events.get()` payload bring the card back to running / queued.
  //      b. Otherwise the latest SSE event drives the visual status.
  //      c. Fall back to whatever the placeholder was initialised with.
  for (const [checkpointId, placeholder] of placeholders) {
    const event = events.get(placeholder.taskId) ?? null
    const placeholderTerminal = isTerminalStatus(placeholder.status)
    const status = placeholderTerminal ? placeholder.status : (event?.status ?? placeholder.status)
    const serverCheckpoint = byId.get(checkpointId)?.checkpoint ?? null
    const checkpoint = serverCheckpoint ?? placeholder.checkpoint
    byId.set(checkpointId, {
      checkpointId,
      sequence: checkpoint?.sequence ?? placeholder.sequence ?? null,
      status,
      event,
      checkpoint,
      error: placeholder.error ?? event?.error ?? null,
      request: placeholder.request,
      taskId: isTerminalStatus(status) ? null : placeholder.taskId,
      cancelRequested: placeholder.cancelRequested,
    })
  }

  // Sort: server checkpoints by sequence ascending, then placeholders in
  // submit order. `Number.POSITIVE_INFINITY` for null pushes them after.
  const insertionFor = (id: string) => placeholders.get(id)?.insertionIndex ?? 0
  return Array.from(byId.values()).sort((a, b) => {
    const aSeq = a.sequence ?? Number.POSITIVE_INFINITY
    const bSeq = b.sequence ?? Number.POSITIVE_INFINITY
    if (aSeq !== bSeq) return aSeq - bSeq
    return insertionFor(a.checkpointId) - insertionFor(b.checkpointId)
  })
}
