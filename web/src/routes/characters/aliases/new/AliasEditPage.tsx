import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ArrowLeft } from 'lucide-react'
import { Link, useNavigate, useParams } from 'react-router'
import { useQueryClient } from '@tanstack/react-query'

import { ApiError } from '@/api/client'
import { uploadCharacterReference, type AliasInputMode } from '@/api/endpoints/aliases'
import { cancelTask, type TaskEvent } from '@/api/endpoints/tasks'
import { useCancelTask } from '@/api/mutations/useCancelTask'
import { useCreateAlias } from '@/api/mutations/useCreateAlias'
import { useUploadMask } from '@/api/mutations/useUploadMask'
import {
  AliasInputPanel,
  InpaintCanvas,
  type InpaintCanvasHandle,
  type MaskPayload,
} from '@/components/aliases'
import { GenericErrorPage, NotFoundPage } from '@/components/composite/ErrorPage'
import { PromptPreviewModal } from '@/components/creation/PromptPreviewModal'
import type { PromptPreviewAliasRequest } from '@/api/endpoints/prompt'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { characterDetailQueryKey, useCharacterDetail } from '@/hooks/useCharacterDetail'
import { useReferenceUpload } from '@/hooks/useReferenceUpload'
import { useTaskStream } from '@/hooks/useTaskStream'
import { AgentError } from '@/lib/agentError'
import { toast } from '@/stores/toastStore'

interface ActiveTask {
  taskId: string
  aliasId: string
}

/**
 * P-06 Alias edit page. The user picks any combination of {text /
 * reference / inpaint} inputs against the character's confirmed Base,
 * which the worker turns into a new Alias via gpt-image-2 (image2image
 * or inpaint depending on `input_mode`). The page stays mounted while
 * the task runs and auto-navigates back to the character detail on
 * completion.
 *
 * Backend contract live in T-031; this page is shipped first (Wave A)
 * and the hooks point at the contract spec'd in
 * `planning/backend/api-shape.md` §5.3 + a mirrored mask-upload
 * endpoint. Adjust if T-031 lands a different shape.
 */
export default function AliasEditPage() {
  const { id: characterId } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const characterQuery = useCharacterDetail(characterId)

  if (!characterId) return <GenericErrorPage description="Character id 缺漏" />
  if (characterQuery.isPending) return <PageSkeleton />
  if (characterQuery.isError) {
    if (characterQuery.error instanceof ApiError && characterQuery.error.status === 404) {
      return <NotFoundPage title="找不到這個角色" description="它可能已被刪除或 URL 寫錯了。" />
    }
    const agent = AgentError.from(characterQuery.error)
    return (
      <GenericErrorPage
        description={agent.message || '無法載入角色'}
        onRetry={() => {
          void characterQuery.refetch()
        }}
      />
    )
  }

  const character = characterQuery.data.character
  if (!character.base) {
    // Spec: "無 base → inline 錯誤頁 + Back to Detail". Aliases are
    // always derived from a confirmed Base (DECISIONS.md §5), so this
    // pre-flight check fails fast rather than rendering a half-state.
    return (
      <section
        data-testid="alias-edit-no-base"
        className="flex flex-col items-center gap-4 rounded-md border border-dashed border-border p-12 text-center"
      >
        <p className="text-sm text-muted-foreground">
          這個角色還沒確立 Base，無法新增 Alias。先回 Character 詳情完成 Base。
        </p>
        <Button asChild variant="outline" size="sm">
          <Link to={`/characters/${characterId}`}>
            <ArrowLeft className="size-4" aria-hidden />回 Character 詳情
          </Link>
        </Button>
      </section>
    )
  }

  return (
    // `key={characterId}` forces a remount when the URL `:id` changes
    // so all the local form state (alias name, freeform note, mask
    // payload, section toggles, in-flight task) resets cleanly. Without
    // it, navigating /characters/A/aliases/new → /characters/B/aliases/
    // new would leak the previous character's input into the next
    // submit (Codex P2 round 3).
    <AliasEditBody
      key={characterId}
      characterId={characterId}
      characterName={character.name}
      baseImageUrl={character.base.image_url ?? ''}
      onCompleted={() => navigate(`/characters/${characterId}`)}
    />
  )
}

interface AliasEditBodyProps {
  characterId: string
  characterName: string
  baseImageUrl: string
  onCompleted: () => void
}

function AliasEditBody({
  characterId,
  characterName,
  baseImageUrl,
  onCompleted,
}: AliasEditBodyProps) {
  // ---- form state -------------------------------------------------------
  const [aliasName, setAliasName] = useState('')
  const [freeformNote, setFreeformNote] = useState('')
  const [textEnabled, setTextEnabled] = useState(true)
  const [referenceEnabled, setReferenceEnabled] = useState(false)
  const [inpaintEnabled, setInpaintEnabled] = useState(false)
  const [maskPayload, setMaskPayload] = useState<MaskPayload | null>(null)
  const [activeTask, setActiveTaskState] = useState<ActiveTask | null>(null)
  const [promptPreviewOpen, setPromptPreviewOpen] = useState(false)

  // `activeTaskRef` mirrors `activeTask` *synchronously* (not via a
  // commit-phase effect) so the unmount cleanup AND the cancel handler can
  // read the latest task without racing the React commit. Without this,
  // clicking [取消] right after [生成 Alias] would see a stale ref and skip
  // the cancel API call entirely.
  const activeTaskRef = useRef<ActiveTask | null>(null)
  const setActiveTask = useCallback((task: ActiveTask | null) => {
    activeTaskRef.current = task
    setActiveTaskState(task)
  }, [])
  // Imperative handle on the canvas so submit can flush any in-flight
  // `canvas.toBlob` before reading the mask. Without this, a fast user
  // can finish a stroke and click submit in the microsecond window
  // before `onMaskChange` has fired, building the request without the
  // just-drawn mask (Codex P1 round 5).
  const inpaintCanvasRef = useRef<InpaintCanvasHandle | null>(null)
  // Synchronous mirror of `maskPayload` so `handleSubmit` reads the
  // fresh value AFTER `flushPendingExport` settles. The state-only
  // version captures `maskPayload` in the callback's closure at render
  // time, so even after the flush updates state, the closure sees the
  // pre-stroke value.
  const maskPayloadRef = useRef<MaskPayload | null>(null)
  // Synchronous in-flight guard for the submit click. `useMutation.isPending`
  // flips on the *next* render after `mutate(...)`, so a fast double-click
  // can pass the disabled-button check twice and fire two POSTs (mask
  // upload + alias create) before React commits the disabled state. The
  // ref short-circuits the second click in the same tick.
  const submitInFlightRef = useRef(false)
  // Set true in the unmount cleanup so the awaiting `handleSubmit` can
  // detect that the page was abandoned mid-POST and fire-and-forget a
  // cancel against the freshly-minted task_id. Without this, leaving via
  // breadcrumb / browser back during the submit window (after POST
  // started but before `task_id` resolved into `activeTaskRef`) would
  // orphan the backend task — it'd run to completion, mint an alias the
  // user never sees, and burn API quota (Codex P1 round 2).
  const isUnmountedRef = useRef(false)

  // Reference upload hook reused with the character-scoped uploader so the
  // multi-file UX (validation, retries, capacity, object-URL cleanup)
  // stays identical to the creation-session reference flow.
  const referenceUpload = useReferenceUpload(characterId, uploadCharacterReference)

  const uploadMaskMutation = useUploadMask(characterId)
  const createAliasMutation = useCreateAlias(characterId)
  const cancelTaskMutation = useCancelTask()
  const queryClient = useQueryClient()

  // ---- submit eligibility ----------------------------------------------
  // We check input *value* presence rather than checkbox state — toggling
  // a section open without filling it shouldn't enable submit.
  const trimmedNote = freeformNote.trim()
  const hasText = trimmedNote.length > 0
  const hasReference = referenceUpload.referenceImageIds.length > 0
  const hasMask = maskPayload !== null
  const hasAnyInput = hasText || hasReference || hasMask

  const isSubmitting =
    uploadMaskMutation.isPending || createAliasMutation.isPending || activeTask !== null

  // ---- SSE terminal handler --------------------------------------------
  const handleTerminal = useCallback(
    (taskId: string, event: TaskEvent) => {
      const current = activeTaskRef.current
      if (!current || current.taskId !== taskId) return
      switch (event.status) {
        case 'completed':
          // Invalidate the character detail query *now* (not at POST
          // time) so the navigation back to /characters/:id lands on a
          // fresh fetch with the new alias visible. Invalidating earlier
          // would have refetched a pre-alias snapshot and served it
          // stale-fresh for staleTime — Codex P2 round 3.
          void queryClient.invalidateQueries({
            queryKey: characterDetailQueryKey(characterId),
          })
          toast.success('Alias 已建立')
          setActiveTask(null)
          onCompleted()
          break
        case 'failed': {
          const agent = event.error
            ? new AgentError(event.error)
            : new AgentError({ code: 'INTERNAL_UNEXPECTED_ERROR', message: 'Alias 生成失敗' })
          toast.agentError(agent)
          setActiveTask(null)
          break
        }
        case 'cancelled':
          // The user clicked 取消 on a running task → mutation returned
          // `cancel_pending` and only emitted the interim「取消中…」toast.
          // Now that the SSE has actually settled to `cancelled`, give
          // the user the explicit success confirmation. The
          // `cancelled_immediately` path is already filtered above by
          // the `activeTaskRef` guard (it clears the ref synchronously),
          // so we won't double-toast.
          toast.success('已取消')
          setActiveTask(null)
          break
        default:
          break
      }
    },
    [characterId, onCompleted, queryClient, setActiveTask],
  )

  const { subscribe } = useTaskStream({ onTerminal: handleTerminal })

  // ---- unmount cancel --------------------------------------------------
  // Spec: 頁面離開（unmount）→ POST /tasks/{id}/cancel.  Fire-and-forget;
  // the user is leaving so an outcome-driven toast would only confuse them.
  // `Promise.resolve(...)` wraps the call so a void-returning mock or a
  // future synchronous shim doesn't make the `.catch` chain blow up.
  useEffect(() => {
    return () => {
      isUnmountedRef.current = true
      const task = activeTaskRef.current
      if (task) {
        Promise.resolve(cancelTask(task.taskId)).catch(() => {
          // Swallow — we're unmounting, nothing useful to surface.
        })
      }
      // The orphan-during-submit case is handled by `handleSubmit`'s
      // post-await unmount check (see below) — the awaited mutateAsync
      // will resolve after this cleanup runs, see `isUnmountedRef.current
      // === true`, and fire its own cancel.
    }
  }, [])

  // ---- input_mode resolution -------------------------------------------
  const computeInputMode = useCallback((): AliasInputMode => {
    const flags = [hasText, hasReference, hasMask]
    const count = flags.filter(Boolean).length
    if (count > 1) return 'mixed'
    if (hasMask) return 'inpaint'
    if (hasReference) return 'image'
    return 'text'
  }, [hasMask, hasReference, hasText])

  // ---- submit ----------------------------------------------------------
  const handleSubmit = useCallback(async () => {
    if (submitInFlightRef.current) return
    const trimmedName = aliasName.trim()
    if (!trimmedName) return

    // Flush any pending mask export before reading the mask — a stroke
    // ended just before submit might still have its `toBlob` in flight,
    // so the closure-captured `maskPayload` could be stale (Codex P1
    // round 5). After the flush, `maskPayloadRef.current` reflects the
    // very latest mask state. We read from the ref (not the closure)
    // because state hasn't re-rendered yet inside this callback's
    // execution.
    await inpaintCanvasRef.current?.flushPendingExport()
    const liveMask = maskPayloadRef.current
    const liveHasMask = liveMask !== null
    const liveHasAnyInput = hasText || hasReference || liveHasMask
    if (!liveHasAnyInput) return
    submitInFlightRef.current = true

    let maskHandle: { mask_id: string } | null = null
    if (liveMask) {
      try {
        const { mask_id } = await uploadMaskMutation.mutateAsync(liveMask.blob)
        maskHandle = { mask_id }
      } catch {
        // The global mutationCache.onError (queryClient.ts) already
        // routes mask-upload errors to the right UI layer per
        // mapAgentErrorToUI; double-toasting here would re-fire on
        // storage/network failures (Codex P2 round 5).
        submitInFlightRef.current = false
        return
      }
    }

    // Bail before sending the alias POST if the user already left while
    // we were uploading the mask. Otherwise we'd mint a backend task
    // (and burn gpt-image-2 quota) just to cancel it via the post-await
    // unmount check on the next line — Codex P1 round 3 wants us to
    // never create the task at all in this case. The orphan mask blob
    // is left behind; backend GCs uploaded masks not referenced by an
    // alias.
    if (isUnmountedRef.current) {
      submitInFlightRef.current = false
      return
    }

    // Re-compute input_mode using the *live* mask state, not the
    // closure-captured `hasMask` from render time — the flush above may
    // have just resolved a fresh mask that the closure doesn't see.
    const flagCount = [hasText, hasReference, liveHasMask].filter(Boolean).length
    const inputMode: AliasInputMode =
      flagCount > 1 ? 'mixed' : liveHasMask ? 'inpaint' : hasReference ? 'image' : 'text'
    const referenceIds = referenceUpload.referenceImageIds

    let response: Awaited<ReturnType<typeof createAliasMutation.mutateAsync>>
    try {
      response = await createAliasMutation.mutateAsync({
        name: trimmedName,
        input_mode: inputMode,
        freeform_note: hasText ? trimmedNote : null,
        reference_image_ids: referenceIds.length > 0 ? referenceIds : null,
        mask: maskHandle,
      })
    } catch {
      // mutationCache (queryClient.ts) already surfaced the AgentError
      // as a toast; nothing more to do here.
      submitInFlightRef.current = false
      return
    } finally {
      submitInFlightRef.current = false
    }

    if (isUnmountedRef.current) {
      // User navigated away while the POST was in flight. The backend
      // accepted the task and is generating; cancel it so we don't burn
      // quota on an alias the user will never see (Codex P1 round 2).
      Promise.resolve(cancelTask(response.task_id)).catch(() => {})
      return
    }

    setActiveTask({ taskId: response.task_id, aliasId: response.alias_id })
    subscribe(response.task_id)
  }, [
    aliasName,
    createAliasMutation,
    hasReference,
    hasText,
    referenceUpload.referenceImageIds,
    setActiveTask,
    subscribe,
    trimmedNote,
    uploadMaskMutation,
  ])

  // ---- cancel ----------------------------------------------------------
  const handleCancel = useCallback(() => {
    const task = activeTaskRef.current
    if (!task) {
      // No active task yet. Either the user hasn't submitted, or submit
      // is mid-POST (no task_id back yet). In either case [取消] just
      // navigates back; if a submit is in flight, the unmount cleanup
      // sets `isUnmountedRef` and the awaiting `handleSubmit` cancels
      // the orphan task once the POST resolves.
      onCompleted()
      return
    }
    cancelTaskMutation.mutate(task.taskId, {
      onSuccess: ({ cancel_outcome }) => {
        switch (cancel_outcome) {
          case 'cancelled_immediately':
            toast.success('已取消')
            setActiveTask(null)
            break
          case 'cancel_pending':
            toast.info('取消中…')
            // Keep activeTask — SSE will deliver the final status.
            break
          case 'too_late_completed':
            // Symmetric with the SSE `completed` branch: the alias was
            // actually created server-side (the cancel raced and lost),
            // so the navigation back to /characters/:id must land on a
            // fresh fetch. Without this, the cached pre-alias snapshot
            // would serve stale-fresh for staleTime — Codex P2 round 4.
            void queryClient.invalidateQueries({
              queryKey: characterDetailQueryKey(characterId),
            })
            toast.warning('來不及取消，Alias 已建立')
            setActiveTask(null)
            onCompleted()
            break
          case 'too_late_failed':
            toast.warning('來不及取消，Alias 生成失敗')
            setActiveTask(null)
            break
        }
      },
    })
  }, [cancelTaskMutation, characterId, onCompleted, queryClient, setActiveTask])

  // ---- prompt preview --------------------------------------------------
  // T-040 ships the modal with a `create_alias` mode that mirrors the
  // submit body shape; we hand it the same input snapshot the user
  // would actually send and let the user audit the merged prompt before
  // hitting [生成 Alias].
  const handleAdvancedView = useCallback(() => {
    setPromptPreviewOpen(true)
  }, [])

  const promptPreviewRequest = useMemo<PromptPreviewAliasRequest>(() => {
    const inputMode = computeInputMode()
    const referenceIds = referenceUpload.referenceImageIds
    return {
      mode: 'create_alias',
      character_id: characterId,
      input_mode: inputMode,
      freeform_note: hasText ? trimmedNote : null,
      reference_image_ids: referenceIds.length > 0 ? referenceIds : null,
      // Preview only needs to know a mask is present; the mask_id is
      // assigned at submit time via the upload endpoint, so the modal
      // gets a synthetic placeholder for the boolean signal.
      mask: maskPayload ? { mask_id: 'pending-upload' } : null,
    }
  }, [
    characterId,
    computeInputMode,
    hasText,
    maskPayload,
    referenceUpload.referenceImageIds,
    trimmedNote,
  ])

  // ---- inpaint canvas mask change --------------------------------------
  const handleMaskChange = useCallback((payload: MaskPayload | null) => {
    // Keep the ref in lockstep with state so post-flush submit reads
    // the fresh value (state updates are async; the ref is sync).
    maskPayloadRef.current = payload
    setMaskPayload(payload)
  }, [])

  // Toggle inpaint section off → drop the mask payload so submit
  // resolution doesn't keep treating the alias as inpaint mode.
  const handleToggleInpaint = useCallback((enabled: boolean) => {
    setInpaintEnabled(enabled)
    if (!enabled) {
      maskPayloadRef.current = null
      setMaskPayload(null)
    }
  }, [])

  const handleToggleText = useCallback((enabled: boolean) => {
    setTextEnabled(enabled)
    if (!enabled) setFreeformNote('')
  }, [])

  const handleToggleReference = useCallback(
    (enabled: boolean) => {
      setReferenceEnabled(enabled)
      if (!enabled) referenceUpload.reset()
    },
    [referenceUpload],
  )

  return (
    <section className="flex flex-col gap-6">
      <Breadcrumb characterId={characterId} name={characterName} />

      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold">新增 Alias</h1>
        <p className="text-sm text-muted-foreground">
          以「{characterName}」的 Base 為基底，建立平行造型變體。
        </p>
      </header>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_24rem]">
        <div className="flex flex-col gap-3">
          <div className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">
            Base
          </div>
          {inpaintEnabled ? (
            <InpaintCanvas
              ref={inpaintCanvasRef}
              baseImageUrl={baseImageUrl}
              enabled
              onMaskChange={handleMaskChange}
            />
          ) : (
            <img
              src={baseImageUrl}
              alt={`${characterName} 的 Base`}
              data-testid="alias-base-image"
              className="w-full max-w-md rounded-md border border-border"
              loading="lazy"
            />
          )}
        </div>

        <AliasInputPanel
          aliasName={aliasName}
          freeformNote={freeformNote}
          textEnabled={textEnabled}
          referenceEnabled={referenceEnabled}
          inpaintEnabled={inpaintEnabled}
          referenceItems={referenceUpload.items}
          isUploading={referenceUpload.isUploading}
          maskCoveragePercent={maskPayload?.coveragePercent ?? null}
          isSubmitting={isSubmitting}
          canSubmit={hasAnyInput}
          isCancelling={cancelTaskMutation.isPending}
          onAliasNameChange={setAliasName}
          onFreeformChange={setFreeformNote}
          onToggleText={handleToggleText}
          onToggleReference={handleToggleReference}
          onToggleInpaint={handleToggleInpaint}
          onAddFiles={(files) => {
            void referenceUpload.addFiles(files)
          }}
          onRemoveImage={referenceUpload.remove}
          onRetryImage={referenceUpload.retry}
          onSubmit={() => {
            void handleSubmit()
          }}
          onCancel={handleCancel}
          onAdvancedView={handleAdvancedView}
        />
      </div>

      <PromptPreviewModal
        isOpen={promptPreviewOpen}
        onClose={() => setPromptPreviewOpen(false)}
        request={promptPreviewRequest}
      />
    </section>
  )
}

function Breadcrumb({ characterId, name }: { characterId: string; name: string }) {
  return (
    <nav aria-label="導覽路徑" className="text-sm">
      <Button asChild variant="ghost" size="sm" className="-ml-2">
        <Link to={`/characters/${characterId}`}>
          <ArrowLeft className="size-4" aria-hidden />
          {name}
        </Link>
      </Button>
      <span className="mx-2 text-muted-foreground">›</span>
      <span className="text-muted-foreground">新增 Alias</span>
    </nav>
  )
}

function PageSkeleton() {
  return (
    <section className="flex flex-col gap-6" data-testid="alias-edit-skeleton">
      <Skeleton className="h-6 w-40" />
      <Skeleton className="h-10 w-64" />
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_24rem]">
        <Skeleton className="h-96 w-full" />
        <Skeleton className="h-96 w-full" />
      </div>
    </section>
  )
}
