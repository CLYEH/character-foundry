import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  createMotion,
  deleteMotion,
  listAliasMotions,
  listBaseMotions,
  type CreateMotionResponse,
  type Motion,
  type MotionListResponse,
} from '@/api/endpoints/motions'
import { useAliasMotions } from '@/api/queries/useAliasMotions'
import { useBaseMotions } from '@/api/queries/useBaseMotions'
import { cancelTask, type CancelTaskResponse } from '@/api/endpoints/tasks'
import { TooltipProvider } from '@/components/ui/tooltip'
import { useAuthStore } from '@/stores/authStore'

import { MotionRow } from '../MotionRow'

vi.mock('@/api/endpoints/motions', async () => {
  const actual =
    await vi.importActual<typeof import('@/api/endpoints/motions')>('@/api/endpoints/motions')
  return {
    ...actual,
    // Mock the parent-type-aware wrapper rather than the two leaf
    // helpers — `createMotion` is what `useGenerateMotion` actually
    // calls, and the leaf functions are referenced internally inside
    // motions.ts so spying on them via the export object misses.
    createMotion: vi.fn(),
    deleteMotion: vi.fn(),
    listBaseMotions: vi.fn(),
    listAliasMotions: vi.fn(),
  }
})

vi.mock('@/api/endpoints/tasks', async () => {
  const actual =
    await vi.importActual<typeof import('@/api/endpoints/tasks')>('@/api/endpoints/tasks')
  return { ...actual, cancelTask: vi.fn() }
})

const sseHandlers = new Map<string, (msg: { data: string }) => void>()
vi.mock('@microsoft/fetch-event-source', () => ({
  fetchEventSource: (
    url: string,
    opts: { onmessage: (msg: { data: string }) => void; signal?: AbortSignal },
  ) => {
    sseHandlers.set(url, opts.onmessage)
    opts.signal?.addEventListener('abort', () => {
      sseHandlers.delete(url)
    })
    return new Promise<void>(() => {})
  },
}))

const sonnerCalls: Array<{ kind: 'success' | 'info' | 'warning' | 'error'; message: string }> = []
vi.mock('sonner', () => ({
  toast: {
    success: (m: string) => sonnerCalls.push({ kind: 'success', message: m }),
    info: (m: string) => sonnerCalls.push({ kind: 'info', message: m }),
    warning: (m: string) => sonnerCalls.push({ kind: 'warning', message: m }),
    error: (m: string) => sonnerCalls.push({ kind: 'error', message: m }),
  },
  Toaster: () => null,
}))

const createMotionMock = vi.mocked(createMotion)
const deleteMotionMock = vi.mocked(deleteMotion)
const cancelTaskMock = vi.mocked(cancelTask)
const listBaseMotionsMock = vi.mocked(listBaseMotions)
const listAliasMotionsMock = vi.mocked(listAliasMotions)

const ME_ID = '11111111-1111-1111-1111-111111111111'
const BASE_ID = 'bbbbbbbb-0000-0000-0000-000000000222'
const ALIAS_ID = 'cccccccc-0000-0000-0000-000000000333'

function makeMotion(overrides: Partial<Motion> = {}): Motion {
  return {
    id: 'motion-1',
    parent: { type: 'base', id: BASE_ID },
    motion_type: 'preset_wave',
    name: '招手歡迎',
    description: null,
    video_url: 'https://video/wave.mp4',
    thumbnail_url: 'https://video/wave-thumb.png',
    duration_ms: 3500,
    created_at: '2026-04-30T11:00:00Z',
    ...overrides,
  }
}

function pushSse(taskId: string, data: object) {
  for (const [url, handler] of sseHandlers.entries()) {
    if (url.includes(`/tasks/${taskId}/stream`)) {
      act(() => handler({ data: JSON.stringify(data) }))
      return
    }
  }
  throw new Error(`no SSE handler for ${taskId}`)
}

interface RenderRowOptions {
  parentType?: 'base' | 'alias'
  parentId?: string
  motions?: Motion[]
  isOwner?: boolean
}

/**
 * Wraps `MotionRow` with the same TanStack Query fetcher its real
 * parent (CharacterDetailPage / AliasRow) uses — so when the row
 * invalidates the motions query, the wrapper refetches via the
 * mocked endpoint and the new list flows back through the prop.
 */
function MotionRowFixture({
  parentType,
  parentId,
  isOwner,
  initialMotions,
}: {
  parentType: 'base' | 'alias'
  parentId: string
  isOwner: boolean
  initialMotions: Motion[]
}) {
  const baseQuery = useBaseMotions(parentType === 'base' ? parentId : undefined)
  const aliasQuery = useAliasMotions(parentType === 'alias' ? parentId : undefined)
  const query = parentType === 'base' ? baseQuery : aliasQuery
  const motions = query.data?.items ?? initialMotions
  return (
    <MotionRow parentType={parentType} parentId={parentId} motions={motions} isOwner={isOwner} />
  )
}

function renderRow(opts: RenderRowOptions = {}) {
  const parentType = opts.parentType ?? 'base'
  const parentId = opts.parentId ?? (parentType === 'base' ? BASE_ID : ALIAS_ID)
  const motions = opts.motions ?? []
  const isOwner = opts.isOwner ?? true
  // Seed the query cache with the initial list so the wrapper renders
  // the supplied motions on first paint without waiting for the mocked
  // endpoint. Endpoint mocks own the post-invalidation refetch.
  if (parentType === 'base') {
    listBaseMotionsMock.mockResolvedValueOnce({ items: motions })
  } else {
    listAliasMotionsMock.mockResolvedValueOnce({ items: motions })
  }
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false, gcTime: 0 },
      mutations: { retry: false },
    },
  })
  return render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <MotionRowFixture
          parentType={parentType}
          parentId={parentId}
          isOwner={isOwner}
          initialMotions={motions}
        />
      </TooltipProvider>
    </QueryClientProvider>,
  )
}

function seedAuth() {
  act(() => {
    useAuthStore.setState({
      accessToken: 'a',
      refreshToken: 'r',
      user: {
        id: ME_ID,
        name: 'Leo',
        email: 'leo@example.com',
        team_id: '22222222-2222-2222-2222-222222222222',
        created_at: '2026-04-28T00:00:00Z',
      },
      expiresAt: Date.now() + 60_000,
    })
  })
}

describe('MotionRow', () => {
  beforeEach(() => {
    seedAuth()
    sseHandlers.clear()
    sonnerCalls.length = 0
    createMotionMock.mockReset()
    deleteMotionMock.mockReset()
    cancelTaskMock.mockReset()
    listBaseMotionsMock.mockReset()
    listAliasMotionsMock.mockReset()
    listBaseMotionsMock.mockResolvedValue({ items: [] } satisfies MotionListResponse)
    listAliasMotionsMock.mockResolvedValue({ items: [] } satisfies MotionListResponse)
  })

  afterEach(() => {
    act(() => {
      useAuthStore.setState({ accessToken: null, refreshToken: null, user: null, expiresAt: null })
    })
  })

  it('starts a base preset generation, streams progress, and lands a completed cell on SSE completed', async () => {
    createMotionMock.mockResolvedValue({
      task_id: 'task-wave',
      motion_id: 'motion-wave-new',
    } satisfies CreateMotionResponse)
    listBaseMotionsMock.mockResolvedValue({
      items: [
        makeMotion({
          id: 'motion-wave-new',
          motion_type: 'preset_wave',
          name: '招手歡迎',
        }),
      ],
    } satisfies MotionListResponse)

    renderRow({ parentType: 'base' })

    fireEvent.click(screen.getByTestId('motion-cell-empty-preset_wave'))

    await waitFor(() =>
      expect(createMotionMock).toHaveBeenCalledWith(
        { type: 'base', id: BASE_ID },
        { motion_type: 'preset_wave', name: '招手歡迎' },
      ),
    )

    // Without an SSE event yet the cell sits in the queued state — covers
    // the gap between POST success and the first streamed frame.
    await screen.findByTestId('motion-cell-queued-preset_wave')

    pushSse('task-wave', { status: 'queued', queue_position: 2 })
    expect(await screen.findByTestId('motion-cell-queued-label-preset_wave')).toHaveTextContent(
      '#2 in queue',
    )

    pushSse('task-wave', { status: 'running', progress: 0.4 })
    expect(await screen.findByTestId('motion-cell-running-preset_wave')).toBeInTheDocument()

    pushSse('task-wave', { status: 'completed' })

    await waitFor(() => {
      expect(screen.getByTestId('motion-cell-completed-motion-wave-new')).toBeInTheDocument()
    })
  })

  it('routes alias preset clicks to the alias parent ref', async () => {
    createMotionMock.mockResolvedValue({
      task_id: 'task-nod',
      motion_id: 'motion-nod-new',
    } satisfies CreateMotionResponse)

    renderRow({ parentType: 'alias' })

    fireEvent.click(screen.getByTestId('motion-cell-empty-preset_nod'))

    await waitFor(() =>
      expect(createMotionMock).toHaveBeenCalledWith(
        { type: 'alias', id: ALIAS_ID },
        { motion_type: 'preset_nod', name: '點頭說明' },
      ),
    )
  })

  it('runs three preset generations in parallel with independent SSE streams', async () => {
    createMotionMock.mockImplementation(async (_parent, body) => ({
      task_id: `task-${body.motion_type}`,
      motion_id: `motion-${body.motion_type}`,
    }))

    renderRow({ parentType: 'base' })

    fireEvent.click(screen.getByTestId('motion-cell-empty-preset_wave'))
    fireEvent.click(screen.getByTestId('motion-cell-empty-preset_nod'))
    fireEvent.click(screen.getByTestId('motion-cell-empty-preset_gesture'))

    // Wait until all 3 SSE subscriptions have actually registered —
    // the mutation onSuccess callback (which calls subscribe) runs in a
    // microtask after the POST resolves, so just waiting on the mock
    // call count would race the subscribe.
    await waitFor(() => expect(createMotionMock).toHaveBeenCalledTimes(3))
    await waitFor(
      () => {
        const open = Array.from(sseHandlers.keys())
        for (const id of ['task-preset_wave', 'task-preset_nod', 'task-preset_gesture']) {
          if (!open.some((u) => u.includes(`/tasks/${id}/stream`))) {
            throw new Error(`waiting for ${id}; have: ${open.join(', ')}`)
          }
        }
      },
      { timeout: 3000 },
    )

    pushSse('task-preset_wave', { status: 'running', progress: 0.2 })
    pushSse('task-preset_nod', { status: 'queued', queue_position: 1 })
    pushSse('task-preset_gesture', { status: 'running', progress: 0.7 })

    await waitFor(() => {
      expect(screen.getByTestId('motion-cell-running-preset_wave')).toBeInTheDocument()
      expect(screen.getByTestId('motion-cell-queued-preset_nod')).toBeInTheDocument()
      expect(screen.getByTestId('motion-cell-running-preset_gesture')).toBeInTheDocument()
    })
  })

  it('surfaces a failed cell with retry that fires another POST', async () => {
    createMotionMock
      .mockResolvedValueOnce({
        task_id: 'task-1',
        motion_id: 'motion-1',
      } satisfies CreateMotionResponse)
      .mockResolvedValueOnce({
        task_id: 'task-2',
        motion_id: 'motion-2',
      } satisfies CreateMotionResponse)

    renderRow({ parentType: 'base' })
    fireEvent.click(screen.getByTestId('motion-cell-empty-preset_wave'))
    await waitFor(() => expect(createMotionMock).toHaveBeenCalledTimes(1))

    pushSse('task-1', {
      status: 'failed',
      error: { code: 'MODEL_RATE_LIMITED', message: '模型忙碌，稍後再試', retryable: true },
    })

    const failedCell = await screen.findByTestId('motion-cell-failed-preset_wave')
    expect(failedCell).toHaveAttribute('aria-label', expect.stringContaining('模型忙碌，稍後再試'))
    expect(sonnerCalls.find((c) => c.kind === 'error')?.message).toBe('模型忙碌，稍後再試')

    fireEvent.click(screen.getByTestId('motion-cell-retry-preset_wave'))

    await waitFor(() => expect(createMotionMock).toHaveBeenCalledTimes(2))
  })

  it('toasts the AgentError when the cancel POST itself fails', async () => {
    createMotionMock.mockResolvedValue({
      task_id: 'task-cancel-fail',
      motion_id: 'motion-cancel-fail',
    } satisfies CreateMotionResponse)
    cancelTaskMock.mockRejectedValue(new Error('Network error'))

    renderRow({ parentType: 'base' })
    fireEvent.click(screen.getByTestId('motion-cell-empty-preset_wave'))
    await waitFor(() => expect(createMotionMock).toHaveBeenCalledTimes(1))
    pushSse('task-cancel-fail', { status: 'running', progress: 0.2 })
    await screen.findByTestId('motion-cell-running-preset_wave')

    fireEvent.click(screen.getByTestId('motion-cell-cancel-preset_wave'))

    await waitFor(() => expect(cancelTaskMock).toHaveBeenCalledWith('task-cancel-fail'))
    // The mutation rejection without an onError handler would silently
    // strand the cell in `running`. Assert the failure surfaces.
    await waitFor(() =>
      expect(sonnerCalls.find((c) => c.kind === 'error')?.message).toBe('Network error'),
    )
    // Cell stays running (not empty) so the user can decide whether to
    // retry the cancel or abandon the page.
    expect(screen.getByTestId('motion-cell-running-preset_wave')).toBeInTheDocument()
  })

  it('settles a cancel_pending cell on the trailing SSE cancelled event', async () => {
    createMotionMock.mockResolvedValue({
      task_id: 'task-cancel-pending',
      motion_id: 'motion-cancel-pending',
    } satisfies CreateMotionResponse)
    cancelTaskMock.mockResolvedValue({
      task: {} as never,
      cancel_outcome: 'cancel_pending',
    } satisfies CancelTaskResponse)

    renderRow({ parentType: 'base' })
    fireEvent.click(screen.getByTestId('motion-cell-empty-preset_wave'))
    await waitFor(() => expect(createMotionMock).toHaveBeenCalledTimes(1))
    pushSse('task-cancel-pending', { status: 'running', progress: 0.4 })
    await screen.findByTestId('motion-cell-running-preset_wave')
    fireEvent.click(screen.getByTestId('motion-cell-cancel-preset_wave'))

    await waitFor(() => expect(sonnerCalls.find((c) => c.kind === 'info')?.message).toBe('取消中…'))
    await screen.findByTestId('motion-cell-cancelling-preset_wave')

    pushSse('task-cancel-pending', { status: 'cancelled' })

    await waitFor(() => {
      expect(screen.getByTestId('motion-cell-empty-preset_wave')).toBeInTheDocument()
    })
    expect(sonnerCalls.find((c) => c.kind === 'success')?.message).toBe('已取消生成')
  })

  it('settles every parallel cancel response (no shared mutation observer drop)', async () => {
    createMotionMock.mockImplementation(async (_parent, body) => ({
      task_id: `task-${body.motion_type}`,
      motion_id: `motion-${body.motion_type}`,
    }))
    cancelTaskMock.mockImplementation(async (taskId: string) => ({
      task: {} as never,
      // Mark each cancel as immediate so dropping a callback would
      // leave the slot visibly stuck (no trailing SSE settles it).
      cancel_outcome: 'cancelled_immediately' as const,
      _taskId: taskId,
    }))

    renderRow({ parentType: 'base' })
    fireEvent.click(screen.getByTestId('motion-cell-empty-preset_wave'))
    fireEvent.click(screen.getByTestId('motion-cell-empty-preset_nod'))
    await waitFor(() => expect(createMotionMock).toHaveBeenCalledTimes(2))
    await waitFor(
      () => {
        const open = Array.from(sseHandlers.keys())
        for (const id of ['task-preset_wave', 'task-preset_nod']) {
          if (!open.some((u) => u.includes(`/tasks/${id}/stream`))) {
            throw new Error('not yet subscribed')
          }
        }
      },
      { timeout: 3000 },
    )
    pushSse('task-preset_wave', { status: 'running', progress: 0.2 })
    pushSse('task-preset_nod', { status: 'running', progress: 0.3 })
    await screen.findByTestId('motion-cell-running-preset_wave')
    await screen.findByTestId('motion-cell-running-preset_nod')

    // Fire both cancels in the same tick. With useMutation's shared
    // observer the first cancel's onSuccess could be dropped, leaving
    // preset_wave stuck in `running`. The direct-cancelTask path must
    // process both responses.
    fireEvent.click(screen.getByTestId('motion-cell-cancel-preset_wave'))
    fireEvent.click(screen.getByTestId('motion-cell-cancel-preset_nod'))
    await waitFor(() => expect(cancelTaskMock).toHaveBeenCalledTimes(2))

    await waitFor(() => {
      expect(screen.getByTestId('motion-cell-empty-preset_wave')).toBeInTheDocument()
      expect(screen.getByTestId('motion-cell-empty-preset_nod')).toBeInTheDocument()
    })
  })

  it('keeps the slot occupied on too_late_completed until the refetched list arrives', async () => {
    createMotionMock.mockResolvedValue({
      task_id: 'task-tlc',
      motion_id: 'motion-tlc',
    } satisfies CreateMotionResponse)
    cancelTaskMock.mockResolvedValue({
      task: {} as never,
      cancel_outcome: 'too_late_completed',
    } satisfies CancelTaskResponse)
    // The post-cancel refetch should land the new motion in the list.
    listBaseMotionsMock.mockResolvedValue({
      items: [
        makeMotion({
          id: 'motion-tlc',
          motion_type: 'preset_wave',
          name: '招手歡迎',
        }),
      ],
    } satisfies MotionListResponse)

    renderRow({ parentType: 'base' })
    fireEvent.click(screen.getByTestId('motion-cell-empty-preset_wave'))
    await waitFor(() => expect(createMotionMock).toHaveBeenCalledTimes(1))
    pushSse('task-tlc', { status: 'running', progress: 0.6 })
    await screen.findByTestId('motion-cell-running-preset_wave')

    fireEvent.click(screen.getByTestId('motion-cell-cancel-preset_wave'))

    // Slot must NOT briefly flip to empty between the cancel response
    // and the refetched list landing — otherwise an accidental click
    // would fire a duplicate POST.
    await waitFor(() =>
      expect(sonnerCalls.find((c) => c.kind === 'warning')?.message).toBe(
        '來不及取消，Motion 已建立',
      ),
    )
    expect(screen.queryByTestId('motion-cell-empty-preset_wave')).not.toBeInTheDocument()

    // Once the refetched list arrives the post-list useEffect drops
    // the pending entry and the completed cell takes over.
    await waitFor(() => {
      expect(screen.getByTestId('motion-cell-completed-motion-tlc')).toBeInTheDocument()
    })
  })

  it('ignores a stale cancel response that lands after retry replaced the task', async () => {
    createMotionMock
      .mockResolvedValueOnce({
        task_id: 'task-old',
        motion_id: 'motion-old',
      } satisfies CreateMotionResponse)
      .mockResolvedValueOnce({
        task_id: 'task-new',
        motion_id: 'motion-new',
      } satisfies CreateMotionResponse)
    // Hold the cancel response so the task can fail + the user can
    // retry before the cancel mutation resolves.
    let resolveCancel: (value: CancelTaskResponse) => void = () => {}
    cancelTaskMock.mockImplementation(
      () => new Promise<CancelTaskResponse>((r) => (resolveCancel = r)),
    )

    renderRow({ parentType: 'base' })
    fireEvent.click(screen.getByTestId('motion-cell-empty-preset_wave'))
    await waitFor(() => expect(createMotionMock).toHaveBeenCalledTimes(1))
    pushSse('task-old', { status: 'running', progress: 0.2 })
    await screen.findByTestId('motion-cell-running-preset_wave')

    fireEvent.click(screen.getByTestId('motion-cell-cancel-preset_wave'))
    await waitFor(() => expect(cancelTaskMock).toHaveBeenCalledWith('task-old'))

    // Old task fails before cancel resolves → cell flips to failed.
    pushSse('task-old', {
      status: 'failed',
      error: { code: 'MODEL_RATE_LIMITED', message: '失敗', retryable: true },
    })
    await screen.findByTestId('motion-cell-failed-preset_wave')

    // User retries → new task in flight.
    fireEvent.click(screen.getByTestId('motion-cell-retry-preset_wave'))
    await waitFor(() => expect(createMotionMock).toHaveBeenCalledTimes(2))
    pushSse('task-new', { status: 'running', progress: 0.1 })
    await screen.findByTestId('motion-cell-running-preset_wave')

    // Now the OLD cancel call resolves with a `too_late_failed`. Without
    // the taskId guard this would clobber the new retry's pending entry
    // and silently drop the running cell.
    resolveCancel({ task: {} as never, cancel_outcome: 'too_late_failed' })

    // Give the mutation's onSuccess a chance to run; assert the cell
    // is still running (new task survived).
    await waitFor(() =>
      expect(screen.getByTestId('motion-cell-running-preset_wave')).toBeInTheDocument(),
    )
    expect(screen.queryByTestId('motion-cell-empty-preset_wave')).not.toBeInTheDocument()
  })

  it('dedups two clicks on the same empty preset cell into one POST', async () => {
    let resolve: (value: CreateMotionResponse) => void = () => {}
    createMotionMock.mockImplementationOnce(
      () => new Promise<CreateMotionResponse>((r) => (resolve = r)),
    )

    renderRow({ parentType: 'base' })
    const cell = screen.getByTestId('motion-cell-empty-preset_wave')
    fireEvent.click(cell)
    // Second click in the same tick lands while the POST is still in
    // flight — the synchronous placeholder reservation should make
    // this a no-op.
    fireEvent.click(cell)

    expect(createMotionMock).toHaveBeenCalledTimes(1)
    resolve({ task_id: 'task-dedup', motion_id: 'motion-dedup' })
    await waitFor(() =>
      expect(
        Array.from(sseHandlers.keys()).some((u) => u.includes('/tasks/task-dedup/stream')),
      ).toBe(true),
    )
    expect(createMotionMock).toHaveBeenCalledTimes(1)
  })

  it('cancels a running task immediately and re-enables the empty cell', async () => {
    createMotionMock.mockResolvedValue({
      task_id: 'task-cancel-now',
      motion_id: 'motion-cancel-now',
    } satisfies CreateMotionResponse)
    cancelTaskMock.mockResolvedValue({
      task: {} as never,
      cancel_outcome: 'cancelled_immediately',
    } satisfies CancelTaskResponse)

    renderRow({ parentType: 'base' })
    fireEvent.click(screen.getByTestId('motion-cell-empty-preset_wave'))
    await waitFor(() => expect(createMotionMock).toHaveBeenCalledTimes(1))

    pushSse('task-cancel-now', { status: 'running', progress: 0.2 })
    await screen.findByTestId('motion-cell-running-preset_wave')

    fireEvent.click(screen.getByTestId('motion-cell-cancel-preset_wave'))

    await waitFor(() => expect(cancelTaskMock).toHaveBeenCalledWith('task-cancel-now'))
    await waitFor(() => {
      expect(screen.getByTestId('motion-cell-empty-preset_wave')).toBeInTheDocument()
    })
    expect(sonnerCalls.find((c) => c.kind === 'success')?.message).toBe('已取消生成')
  })

  it('deletes a completed motion via the dropdown → confirm dialog', async () => {
    deleteMotionMock.mockResolvedValue(undefined)
    listBaseMotionsMock.mockResolvedValue({ items: [] } satisfies MotionListResponse)

    renderRow({
      parentType: 'base',
      motions: [
        makeMotion({
          id: 'motion-existing',
          motion_type: 'preset_wave',
          name: '招手歡迎',
        }),
      ],
    })

    fireEvent.click(screen.getByTestId('motion-cell-menu-motion-existing'))
    const deleteItem = await screen.findByTestId('motion-cell-delete-motion-existing')
    fireEvent.click(deleteItem)

    expect(await screen.findByTestId('motion-delete-confirm')).toHaveTextContent(
      '刪除 Motion「招手歡迎」？',
    )
    fireEvent.click(screen.getByTestId('motion-delete-confirm-action'))

    await waitFor(() => expect(deleteMotionMock).toHaveBeenCalledWith('motion-existing'))
  })

  it('does not let the user click an already-generated preset', () => {
    renderRow({
      parentType: 'base',
      motions: [
        makeMotion({
          id: 'motion-already',
          motion_type: 'preset_wave',
        }),
      ],
    })

    expect(screen.queryByTestId('motion-cell-empty-preset_wave')).not.toBeInTheDocument()
    expect(screen.getByTestId('motion-cell-completed-motion-already')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('motion-cell-completed-motion-already'))
    expect(createMotionMock).not.toHaveBeenCalled()
  })

  it('disables every preset slot for non-owners', () => {
    renderRow({ parentType: 'base', isOwner: false })
    for (const type of [
      'preset_wave',
      'preset_nod',
      'preset_gesture',
      'preset_happy',
      'preset_idle',
    ] as const) {
      expect(screen.getByTestId(`motion-cell-empty-${type}`)).toBeDisabled()
    }
    fireEvent.click(screen.getByTestId('motion-cell-empty-preset_wave'))
    expect(createMotionMock).not.toHaveBeenCalled()
  })

  it('hides the [⋯] menu on completed cells when the viewer is not the owner', () => {
    renderRow({
      parentType: 'base',
      isOwner: false,
      motions: [
        makeMotion({
          id: 'motion-readonly',
          motion_type: 'preset_wave',
        }),
      ],
    })
    expect(screen.queryByTestId('motion-cell-menu-motion-readonly')).not.toBeInTheDocument()
  })

  it('renders the Sprint-3 count line and a fetch-error band when supplied', () => {
    const motion = makeMotion({ id: 'motion-counted', motion_type: 'preset_wave' })
    renderRow({ parentType: 'alias', motions: [motion] })
    const row = screen.getByTestId(`motion-row-alias-${ALIAS_ID}`)
    expect(within(row).getByText(/1\/5 預設 \+ 0 自訂/)).toBeInTheDocument()
  })

  it('shows the row error band when errorMessage is supplied', () => {
    render(
      <QueryClientProvider
        client={
          new QueryClient({
            defaultOptions: {
              queries: { retry: false, refetchOnWindowFocus: false, gcTime: 0 },
              mutations: { retry: false },
            },
          })
        }
      >
        <TooltipProvider>
          <MotionRow
            parentType="base"
            parentId={BASE_ID}
            motions={[]}
            isOwner
            errorMessage="伺服器忙碌"
          />
        </TooltipProvider>
      </QueryClientProvider>,
    )
    expect(screen.getByTestId(`motion-row-error-base-${BASE_ID}`)).toHaveTextContent('伺服器忙碌')
  })
})
