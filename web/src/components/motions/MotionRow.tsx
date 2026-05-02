import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Plus } from 'lucide-react'

import {
  createMotion,
  PRESET_MOTION_TYPES,
  type CreateMotionResponse,
  type Motion,
  type MotionParentRef,
  type MotionParentType,
  type PresetMotionType,
} from '@/api/endpoints/motions'
import { cancelTask, type TaskEvent } from '@/api/endpoints/tasks'
import { useDeleteMotion } from '@/api/mutations/useDeleteMotion'
import { aliasMotionsQueryKey } from '@/api/queries/useAliasMotions'
import { baseMotionsQueryKey } from '@/api/queries/useBaseMotions'
import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { useTaskStream } from '@/hooks/useTaskStream'
import { AgentError } from '@/lib/agentError'
import { useAuthStore } from '@/stores/authStore'
import { toast } from '@/stores/toastStore'
import { PRESET_LABELS, PRESET_NAMES } from '@/constants/preset_motions'
import { CustomMotionModal } from './CustomMotionModal'
import { MotionCell } from './MotionCell'
import { MotionDeleteConfirm } from './MotionDeleteConfirm'
import { MotionLightbox } from './MotionLightbox'

export interface MotionRowProps {
  parentType: MotionParentType
  parentId: string
  motions: Motion[]
  isOwner: boolean
  /** Loading skeleton while the motions list is fetching. */
  isLoading?: boolean
  /**
   * Surface a fetch failure on the row so we don't silently coerce a
   * load error into "0 motions generated" — the slots themselves still
   * render (they're click-to-generate affordances) but the user sees
   * the error band so they know the displayed counts are unreliable.
   */
  errorMessage?: string | null
}

interface PresetPending {
  taskId: string
  motionId: string
  /** Sticky `failed` flag so the cell shows the failed state until the user retries / dismisses. */
  failed?: { error: AgentError }
  /** Sticky `cancelling` flag while the worker is finishing the abort. */
  cancelling?: boolean
}

interface CustomPending {
  taskId: string
  motionId: string
  /** User-supplied name; rendered as the queued / running cell's label. */
  name: string
  /** Original description retained so retry can refire the same payload. */
  description: string
  failed?: { error: AgentError }
  cancelling?: boolean
}

/**
 * P-05 motion strip rendered under each Base / Alias card.
 *
 * Owns the per-cell generation lifecycle (POST + SSE) for preset
 * motions: empty → queued/running → completed (motion list refetched)
 * or failed (sticky cell with retry / dismiss). The component stays
 * mounted while the row's parent card is visible so multiple cells can
 * stream in parallel without losing each other's progress.
 *
 * Custom motions take the same SSE / cancel path through a Modal M-02
 * trigger; the resulting pending cells are keyed by `motion_id` because
 * unlike preset slots, the user can spawn multiple custom motions per
 * parent.
 */
export function MotionRow({
  parentType,
  parentId,
  motions,
  isOwner,
  isLoading,
  errorMessage,
}: MotionRowProps) {
  const parent = useMemo<MotionParentRef>(
    () => ({ type: parentType, id: parentId }),
    [parentType, parentId],
  )
  const queryClient = useQueryClient()
  const userId = useAuthStore((s) => s.user?.id)
  const deleteMutation = useDeleteMotion(parent)

  const [lightboxMotion, setLightboxMotion] = useState<Motion | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<Motion | null>(null)
  const [isCustomModalOpen, setIsCustomModalOpen] = useState(false)
  const [pendingPresets, setPendingPresets] = useState<Record<string, PresetPending>>({})
  const [pendingCustom, setPendingCustom] = useState<Record<string, CustomPending>>({})
  // Synchronous mirrors of `pendingPresets` / `pendingCustom` so SSE
  // handlers (which fire outside React's commit cycle via the
  // `useTaskStream` map) can read the latest map without racing render.
  // Without these, fast clicks can reduce stale closures back into the
  // state and stomp entries that just landed.
  const pendingRef = useRef<Record<string, PresetPending>>({})
  const customRef = useRef<Record<string, CustomPending>>({})
  const updatePending = useCallback(
    (updater: (prev: Record<string, PresetPending>) => Record<string, PresetPending>) => {
      const next = updater(pendingRef.current)
      pendingRef.current = next
      setPendingPresets(next)
    },
    [],
  )
  const updateCustom = useCallback(
    (updater: (prev: Record<string, CustomPending>) => Record<string, CustomPending>) => {
      const next = updater(customRef.current)
      customRef.current = next
      setPendingCustom(next)
    },
    [],
  )

  const motionsQueryKey = useMemo(
    () =>
      parentType === 'alias'
        ? aliasMotionsQueryKey(userId, parentId)
        : baseMotionsQueryKey(userId, parentId),
    [parentType, parentId, userId],
  )

  const handleTerminal = useCallback(
    (taskId: string, event: TaskEvent) => {
      const presetEntry = Object.entries(pendingRef.current).find(([, v]) => v.taskId === taskId)
      const customEntry = Object.entries(customRef.current).find(([, v]) => v.taskId === taskId)
      if (!presetEntry && !customEntry) return

      switch (event.status) {
        case 'completed':
          // Kick the refetch; the actual pending → completed handoff is
          // driven by the `motions` prop landing the new row (see the
          // post-list effect below). Removing the pending entry here
          // would briefly flip the cell back to empty if the refetched
          // list took an extra tick to arrive.
          void queryClient.invalidateQueries({ queryKey: motionsQueryKey })
          break
        case 'failed': {
          const agent = event.error
            ? new AgentError(event.error)
            : new AgentError({ code: 'INTERNAL_UNEXPECTED_ERROR', message: '生成失敗' })
          if (presetEntry) {
            const [presetKey] = presetEntry
            updatePending((prev) => {
              const current = prev[presetKey]
              if (!current || current.taskId !== taskId) return prev
              return {
                ...prev,
                [presetKey]: { ...current, failed: { error: agent }, cancelling: false },
              }
            })
          }
          if (customEntry) {
            const [customKey] = customEntry
            updateCustom((prev) => {
              const current = prev[customKey]
              if (!current || current.taskId !== taskId) return prev
              return {
                ...prev,
                [customKey]: { ...current, failed: { error: agent }, cancelling: false },
              }
            })
          }
          toast.agentError(agent)
          break
        }
        case 'cancelled':
          if (presetEntry) {
            const [presetKey] = presetEntry
            updatePending((prev) => {
              const next = { ...prev }
              delete next[presetKey]
              return next
            })
          }
          if (customEntry) {
            const [customKey] = customEntry
            updateCustom((prev) => {
              const next = { ...prev }
              delete next[customKey]
              return next
            })
          }
          toast.success('已取消生成')
          break
        default:
          break
      }
    },
    [motionsQueryKey, queryClient, updateCustom, updatePending],
  )

  const { events, subscribe, unsubscribe } = useTaskStream({ onTerminal: handleTerminal })

  // Drop pending entries whose motion has actually landed in the
  // refetched list — this is what flips a `completed` cell from
  // queued/running to the completed cell without a flicker. Doing the
  // delete here (driven by the prop) instead of inside the SSE
  // terminal handler closes the race between the SSE arriving and
  // the parent passing the new motion list down.
  useEffect(() => {
    let presetMutated = false
    const nextPreset = { ...pendingRef.current }
    for (const [key, pending] of Object.entries(pendingRef.current)) {
      if (pending.motionId && motions.some((m) => m.id === pending.motionId)) {
        delete nextPreset[key]
        presetMutated = true
      }
    }
    if (presetMutated) {
      pendingRef.current = nextPreset
      setPendingPresets(nextPreset)
    }

    let customMutated = false
    const nextCustom = { ...customRef.current }
    for (const [key, pending] of Object.entries(customRef.current)) {
      if (pending.motionId && motions.some((m) => m.id === pending.motionId)) {
        delete nextCustom[key]
        customMutated = true
      }
    }
    if (customMutated) {
      customRef.current = nextCustom
      setPendingCustom(nextCustom)
    }
  }, [motions])

  const startPresetGeneration = useCallback(
    (presetType: PresetMotionType) => {
      if (!isOwner) return
      // Bail if the slot is already in flight (queued / running /
      // cancelling). `failed` is treated as claimable here because the
      // sticky failed cell exposes its own retry path that must replace
      // the stale entry — without that escape hatch a one-off failure
      // would lock the slot until unmount.
      const existing = pendingRef.current[presetType]
      if (existing && !existing.failed) return

      // Reserve the slot synchronously with a placeholder entry so a
      // second click in the same tick (before the POST resolves) hits
      // the guard above instead of firing a duplicate POST.
      updatePending((prev) => ({
        ...prev,
        [presetType]: { taskId: '', motionId: '' },
      }))

      // Call createMotion directly instead of going through
      // `useMutation`. The 5 preset slots can fire concurrently and we
      // don't want any aggregate "is anything pending" UI state — each
      // slot tracks its own lifecycle inside `pendingPresets`. Per-slot
      // `useMutation` instances would also work but add a hook per slot
      // and obscure the fact that the request is fire-and-forget; the
      // direct promise keeps the call site honest about that.
      createMotion(parent, { motion_type: presetType, name: PRESET_NAMES[presetType] })
        .then((response) => {
          updatePending((prev) => ({
            ...prev,
            [presetType]: { taskId: response.task_id, motionId: response.motion_id },
          }))
          subscribe(response.task_id)
        })
        .catch((err) => {
          // POST itself failed (no task spawned). Drop the placeholder
          // so the user can retry the empty cell, and surface the
          // error as a toast.
          updatePending((prev) => {
            const next = { ...prev }
            delete next[presetType]
            return next
          })
          toast.agentError(AgentError.from(err))
        })
    },
    [isOwner, parent, subscribe, updatePending],
  )

  const handleCancel = useCallback(
    (presetType: PresetMotionType) => {
      const entry = pendingRef.current[presetType]
      if (!entry || entry.failed) return

      // Call cancelTask directly instead of going through `useMutation`.
      // Same reasoning as `createMotion` above: when the user cancels
      // multiple running slots in quick succession, every cancel
      // response must run its own callbacks. A shared
      // `useMutation` observer can drop the older call's per-call
      // `onSuccess` (Codex P1 #64). Especially load-bearing for
      // `cancelled_immediately`, which is already terminal server-
      // side and may not emit a trailing SSE — if its callback gets
      // dropped, the slot would stay stuck in `running` forever.
      const slotStillOurs = (
        mutator: (prev: Record<string, PresetPending>) => Record<string, PresetPending>,
      ) =>
        updatePending((prev) => {
          const current = prev[presetType]
          if (!current || current.taskId !== entry.taskId) return prev
          return mutator(prev)
        })
      const dropSlot = () =>
        slotStillOurs((prev) => {
          const next = { ...prev }
          delete next[presetType]
          return next
        })

      cancelTask(entry.taskId)
        .then(({ cancel_outcome }) => {
          switch (cancel_outcome) {
            case 'cancelled_immediately':
              unsubscribe(entry.taskId)
              dropSlot()
              toast.success('已取消生成')
              break
            case 'cancel_pending':
              slotStillOurs((prev) => ({
                ...prev,
                [presetType]: { ...prev[presetType], cancelling: true },
              }))
              toast.info('取消中…')
              break
            case 'too_late_completed':
              // The motion was actually created — let the refetched
              // list drive the pending → completed handoff (same path
              // as the SSE `completed` branch), otherwise the slot
              // briefly flips to empty and lets the user fire a second
              // generate click before the list lands.
              unsubscribe(entry.taskId)
              void queryClient.invalidateQueries({ queryKey: motionsQueryKey })
              toast.warning('來不及取消，Motion 已建立')
              break
            case 'too_late_failed':
              unsubscribe(entry.taskId)
              dropSlot()
              toast.warning('來不及取消，Motion 生成失敗')
              break
          }
        })
        .catch((err) => {
          // Without an explicit handler the rejection would be
          // swallowed: cell stays in `running`, cancel button keeps
          // refiring the same broken POST, no signal to the user.
          toast.agentError(AgentError.from(err))
        })
    },
    [motionsQueryKey, queryClient, unsubscribe, updatePending],
  )

  const handleRetry = useCallback(
    (presetType: PresetMotionType) => {
      // Drop the failed sticky entry so the cell flips back to empty
      // before the new POST lands; if the POST fails synchronously the
      // user is back to an empty cell rather than two stacked errors.
      updatePending((prev) => {
        const next = { ...prev }
        delete next[presetType]
        return next
      })
      startPresetGeneration(presetType)
    },
    [startPresetGeneration, updatePending],
  )

  const handleDismissFailed = useCallback(
    (presetType: PresetMotionType) => {
      updatePending((prev) => {
        const next = { ...prev }
        delete next[presetType]
        return next
      })
    },
    [updatePending],
  )

  const handleCustomSuccess = useCallback(
    (response: CreateMotionResponse, name: string, description: string) => {
      updateCustom((prev) => ({
        ...prev,
        [response.motion_id]: {
          taskId: response.task_id,
          motionId: response.motion_id,
          name,
          description,
        },
      }))
      subscribe(response.task_id)
      setIsCustomModalOpen(false)
    },
    [subscribe, updateCustom],
  )

  const handleCustomCancel = useCallback(
    (motionId: string) => {
      const entry = customRef.current[motionId]
      if (!entry || entry.failed) return

      const slotStillOurs = (
        mutator: (prev: Record<string, CustomPending>) => Record<string, CustomPending>,
      ) =>
        updateCustom((prev) => {
          const current = prev[motionId]
          if (!current || current.taskId !== entry.taskId) return prev
          return mutator(prev)
        })
      const dropSlot = () =>
        slotStillOurs((prev) => {
          const next = { ...prev }
          delete next[motionId]
          return next
        })

      cancelTask(entry.taskId)
        .then(({ cancel_outcome }) => {
          switch (cancel_outcome) {
            case 'cancelled_immediately':
              unsubscribe(entry.taskId)
              dropSlot()
              toast.success('已取消生成')
              break
            case 'cancel_pending':
              slotStillOurs((prev) => ({
                ...prev,
                [motionId]: { ...prev[motionId], cancelling: true },
              }))
              toast.info('取消中…')
              break
            case 'too_late_completed':
              unsubscribe(entry.taskId)
              void queryClient.invalidateQueries({ queryKey: motionsQueryKey })
              toast.warning('來不及取消，Motion 已建立')
              break
            case 'too_late_failed':
              unsubscribe(entry.taskId)
              dropSlot()
              toast.warning('來不及取消，Motion 生成失敗')
              break
          }
        })
        .catch((err) => {
          toast.agentError(AgentError.from(err))
        })
    },
    [motionsQueryKey, queryClient, unsubscribe, updateCustom],
  )

  const handleCustomDismiss = useCallback(
    (motionId: string) => {
      updateCustom((prev) => {
        const next = { ...prev }
        delete next[motionId]
        return next
      })
    },
    [updateCustom],
  )

  const presetByType = useMemo(() => {
    const map = new Map<PresetMotionType, Motion>()
    for (const motion of motions) {
      if (motion.motion_type !== 'custom') {
        map.set(motion.motion_type, motion)
      }
    }
    return map
  }, [motions])
  const customMotions = useMemo(() => motions.filter((m) => m.motion_type === 'custom'), [motions])

  const presetGenerated = presetByType.size
  const customCount = customMotions.length

  const handleDelete = useCallback(
    (motion: Motion) => {
      deleteMutation.mutate(
        { motionId: motion.id },
        {
          onSuccess: () => {
            setDeleteTarget(null)
          },
        },
      )
    },
    [deleteMutation],
  )

  const deleteError = deleteMutation.error ? AgentError.from(deleteMutation.error).message : null

  return (
    <div data-testid={`motion-row-${parentType}-${parentId}`} className="flex flex-col gap-3">
      {errorMessage ? (
        <p
          role="alert"
          data-testid={`motion-row-error-${parentType}-${parentId}`}
          className="rounded border border-destructive/40 bg-destructive/5 px-2 py-1 text-xs text-destructive"
        >
          無法載入動作：{errorMessage}
        </p>
      ) : (
        <p className="text-xs text-muted-foreground">
          Motions ({presetGenerated}/5 預設 + {customCount} 自訂)
        </p>
      )}
      <ul className="flex flex-wrap gap-2" aria-label="預設動作">
        {PRESET_MOTION_TYPES.map((type) => {
          const existing = presetByType.get(type)
          if (existing) {
            return (
              <li key={type}>
                <MotionCell
                  variant="completed"
                  motion={existing}
                  isOwner={isOwner}
                  onPlay={setLightboxMotion}
                  onDelete={isOwner ? setDeleteTarget : undefined}
                />
              </li>
            )
          }
          const pending = pendingPresets[type]
          const event = pending ? events.get(pending.taskId) : undefined
          return (
            <li key={type}>
              {renderPresetCell({
                slotId: type,
                label: PRESET_LABELS[type],
                isOwner,
                pending,
                event,
                onTrigger: () => startPresetGeneration(type),
                onCancel: () => handleCancel(type),
                onRetry: () => handleRetry(type),
                onDismiss: () => handleDismissFailed(type),
              })}
            </li>
          )
        })}
      </ul>

      <div className="flex flex-col gap-2">
        <p className="text-xs text-muted-foreground">自訂 motions</p>
        <ul className="flex flex-wrap gap-2" aria-label="自訂動作">
          {customMotions.map((motion) => (
            <li key={motion.id}>
              <MotionCell
                variant="completed"
                motion={motion}
                isOwner={isOwner}
                onPlay={setLightboxMotion}
                onDelete={isOwner ? setDeleteTarget : undefined}
              />
            </li>
          ))}
          {Object.values(pendingCustom).map((pending) => {
            const event = events.get(pending.taskId)
            return (
              <li key={pending.motionId}>
                {renderCustomCell({
                  pending,
                  event,
                  isOwner,
                  onCancel: () => handleCustomCancel(pending.motionId),
                  onDismiss: () => handleCustomDismiss(pending.motionId),
                })}
              </li>
            )
          })}
          <li>
            {isOwner ? (
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => setIsCustomModalOpen(true)}
                data-testid={`motion-add-custom-${parentType}-${parentId}`}
              >
                <Plus className="size-3.5" aria-hidden />
                自訂動作
              </Button>
            ) : (
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="inline-flex">
                    <Button
                      type="button"
                      variant="secondary"
                      size="sm"
                      disabled
                      data-testid={`motion-add-custom-${parentType}-${parentId}`}
                    >
                      <Plus className="size-3.5" aria-hidden />
                      自訂動作
                    </Button>
                  </span>
                </TooltipTrigger>
                <TooltipContent>僅 owner 可操作</TooltipContent>
              </Tooltip>
            )}
          </li>
        </ul>
      </div>

      {isLoading ? (
        <p className="text-xs text-muted-foreground" data-testid="motion-row-loading">
          載入動作中…
        </p>
      ) : null}

      <MotionLightbox motion={lightboxMotion} onClose={() => setLightboxMotion(null)} />
      <MotionDeleteConfirm
        isOpen={deleteTarget !== null}
        motionName={deleteTarget?.name ?? ''}
        isPending={deleteMutation.isPending}
        errorMessage={deleteError}
        onClose={() => {
          if (!deleteMutation.isPending) {
            setDeleteTarget(null)
            deleteMutation.reset()
          }
        }}
        onConfirm={() => {
          if (deleteTarget) handleDelete(deleteTarget)
        }}
      />
      <CustomMotionModal
        isOpen={isCustomModalOpen}
        parent={parent}
        onClose={() => setIsCustomModalOpen(false)}
        onSuccess={handleCustomSuccess}
      />
    </div>
  )
}

interface RenderPresetCellArgs {
  slotId: PresetMotionType
  label: string
  isOwner: boolean
  pending: PresetPending | undefined
  event: TaskEvent | undefined
  onTrigger: () => void
  onCancel: () => void
  onRetry: () => void
  onDismiss: () => void
}

function renderPresetCell({
  slotId,
  label,
  isOwner,
  pending,
  event,
  onTrigger,
  onCancel,
  onRetry,
  onDismiss,
}: RenderPresetCellArgs) {
  if (!pending) {
    return (
      <MotionCell
        variant="empty"
        slotId={slotId}
        label={label}
        isOwner={isOwner}
        onTrigger={onTrigger}
      />
    )
  }
  if (pending.failed) {
    return (
      <MotionCell
        variant="failed"
        slotId={slotId}
        label={label}
        isOwner={isOwner}
        errorMessage={pending.failed.error.message || '生成失敗'}
        onRetry={isOwner ? onRetry : undefined}
        onDismiss={isOwner ? onDismiss : undefined}
      />
    )
  }
  if (pending.cancelling) {
    return <MotionCell variant="cancelling" slotId={slotId} label={label} />
  }
  // Cancel is only meaningful once the POST has minted a task id — the
  // placeholder reservation between the click and the response can't
  // be cancelled server-side, so we hide the affordance until the SSE
  // can route progress events.
  const cancelHandler = isOwner && pending.taskId ? onCancel : undefined
  const status = event?.status
  if (status === 'running') {
    return (
      <MotionCell
        variant="running"
        slotId={slotId}
        label={label}
        isOwner={isOwner}
        progress={event?.progress ?? null}
        onCancel={cancelHandler}
      />
    )
  }
  // Default to queued — covers the gap between POST returning and the
  // first SSE frame arriving, plus explicit `queued` events.
  return (
    <MotionCell
      variant="queued"
      slotId={slotId}
      label={label}
      isOwner={isOwner}
      queuePosition={event?.queue_position ?? null}
      onCancel={cancelHandler}
    />
  )
}

interface RenderCustomCellArgs {
  pending: CustomPending
  event: TaskEvent | undefined
  isOwner: boolean
  onCancel: () => void
  onDismiss: () => void
}

function renderCustomCell({ pending, event, isOwner, onCancel, onDismiss }: RenderCustomCellArgs) {
  // The custom map is keyed by motion_id, which doubles as the cell's
  // slotId so MotionCell's testids stay unique across preset / custom
  // sections. Label falls back to the user-supplied name.
  const slotId = pending.motionId
  const label = pending.name
  if (pending.failed) {
    return (
      <MotionCell
        variant="failed"
        slotId={slotId}
        label={label}
        isOwner={isOwner}
        errorMessage={pending.failed.error.message || '生成失敗'}
        onDismiss={isOwner ? onDismiss : undefined}
      />
    )
  }
  if (pending.cancelling) {
    return <MotionCell variant="cancelling" slotId={slotId} label={label} />
  }
  const cancelHandler = isOwner && pending.taskId ? onCancel : undefined
  const status = event?.status
  if (status === 'running') {
    return (
      <MotionCell
        variant="running"
        slotId={slotId}
        label={label}
        isOwner={isOwner}
        progress={event?.progress ?? null}
        onCancel={cancelHandler}
      />
    )
  }
  return (
    <MotionCell
      variant="queued"
      slotId={slotId}
      label={label}
      isOwner={isOwner}
      queuePosition={event?.queue_position ?? null}
      onCancel={cancelHandler}
    />
  )
}
