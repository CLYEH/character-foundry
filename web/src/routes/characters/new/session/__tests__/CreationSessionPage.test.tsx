import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import CreationSessionPage from '../CreationSessionPage'
import { cancelTask, type CancelTaskResponse, type TaskEvent } from '@/api/endpoints/tasks'
import {
  createCheckpoint,
  getCreationSession,
  type Checkpoint,
  type CreateCheckpointResponse,
  type CreationSessionDetail,
} from '@/api/endpoints/checkpoints'
import { TooltipProvider } from '@/components/ui/tooltip'
import { useAuthStore } from '@/stores/authStore'

// ---------------------------------------------------------------------------
// Endpoint mocks
// ---------------------------------------------------------------------------

vi.mock('@/api/endpoints/checkpoints', async () => {
  const actual = await vi.importActual<typeof import('@/api/endpoints/checkpoints')>(
    '@/api/endpoints/checkpoints',
  )
  return {
    ...actual,
    getCreationSession: vi.fn(),
    createCheckpoint: vi.fn(),
  }
})

vi.mock('@/api/endpoints/tasks', async () => {
  const actual =
    await vi.importActual<typeof import('@/api/endpoints/tasks')>('@/api/endpoints/tasks')
  return { ...actual, cancelTask: vi.fn() }
})

// Mocking @microsoft/fetch-event-source lets the real useTaskStream run with
// a synthetic transport — `pushSse` below drives messages into the captured
// onmessage callback so tests assert on real placeholder → final transitions.
const sseHandlers = new Map<string, (msg: { data: string }) => void>()

vi.mock('@microsoft/fetch-event-source', () => ({
  fetchEventSource: (url: string, opts: { onmessage: (msg: { data: string }) => void }) => {
    sseHandlers.set(url, opts.onmessage)
    return new Promise<void>(() => {
      /* never resolves — abort comes from the AbortController in the hook */
    })
  },
}))

const sonnerCalls: Array<{ kind: string; message: string }> = []
vi.mock('sonner', async () => {
  const actual = await vi.importActual<typeof import('sonner')>('sonner')
  const make = (kind: string) => (message: string) => {
    sonnerCalls.push({ kind, message })
    return 0
  }
  return {
    ...actual,
    toast: Object.assign(make('default'), {
      success: make('success'),
      info: make('info'),
      warning: make('warning'),
      error: make('error'),
      dismiss: vi.fn(),
    }),
  }
})

const getCreationSessionMock = vi.mocked(getCreationSession)
const createCheckpointMock = vi.mocked(createCheckpoint)
const cancelTaskMock = vi.mocked(cancelTask)

// ---------------------------------------------------------------------------
// Fixture helpers
// ---------------------------------------------------------------------------

const SESSION_ID = '55555555-5555-5555-5555-555555555555'
const ME_ID = '11111111-1111-1111-1111-111111111111'

function makeSessionDetail(checkpoints: Checkpoint[] = []): CreationSessionDetail {
  return {
    session: {
      id: SESSION_ID,
      character_id: 'char-id',
      input_mode: 'template',
      status: 'in_progress',
      checkpoint_count: checkpoints.length,
      created_at: '2026-04-28T08:00:00Z',
      completed_at: null,
    },
    checkpoints,
  }
}

function makeCheckpoint(overrides: Partial<Checkpoint> = {}): Checkpoint {
  return {
    id: 'cp-existing',
    creation_session_id: SESSION_ID,
    sequence: 1,
    prompt_summary: '女性・水墨畫風格',
    output_image_url: 'https://img/full.png',
    thumbnail_url: 'https://img/thumb.png',
    selected_as_base: false,
    created_at: '2026-04-28T08:05:00Z',
    ...overrides,
  }
}

function pushSse(taskId: string, event: TaskEvent) {
  // Find the handler whose URL matches the task id — `BASE_URL` defaults to
  // empty string in tests, so the URL is `/v1/tasks/{id}/stream`.
  for (const [url, handler] of sseHandlers) {
    if (url.includes(`/tasks/${taskId}/stream`)) {
      act(() => handler({ data: JSON.stringify(event) }))
      return
    }
  }
  throw new Error(`No SSE handler registered for task ${taskId}`)
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false, gcTime: 0 },
      mutations: { retry: false },
    },
  })
  return render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <MemoryRouter initialEntries={[`/characters/new/session/${SESSION_ID}`]}>
          <Routes>
            <Route path="/" element={<div>dashboard</div>} />
            <Route path="/characters/new/session/:id" element={<CreationSessionPage />} />
          </Routes>
        </MemoryRouter>
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

beforeEach(() => {
  seedAuth()
  sseHandlers.clear()
  sonnerCalls.length = 0
  getCreationSessionMock.mockReset()
  createCheckpointMock.mockReset()
  cancelTaskMock.mockReset()
})

afterEach(() => {
  act(() => {
    useAuthStore.setState({ accessToken: null, refreshToken: null, user: null, expiresAt: null })
  })
})

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('CreationSessionPage', () => {
  it('shows the empty state when the session has no checkpoints yet', async () => {
    getCreationSessionMock.mockResolvedValue(makeSessionDetail([]))
    renderPage()
    expect(await screen.findByTestId('checkpoint-list-empty')).toBeInTheDocument()
  })

  it('renders existing checkpoints from the GET payload', async () => {
    getCreationSessionMock.mockResolvedValue(
      makeSessionDetail([
        makeCheckpoint({ id: 'cp-1', sequence: 1 }),
        makeCheckpoint({ id: 'cp-2', sequence: 2 }),
      ]),
    )
    renderPage()
    expect(await screen.findByTestId('checkpoint-card-cp-1')).toBeInTheDocument()
    expect(screen.getByTestId('checkpoint-card-cp-2')).toBeInTheDocument()
  })

  it('disables 生成 until a session loads, then enables for fresh submission', async () => {
    getCreationSessionMock.mockResolvedValue(makeSessionDetail([]))
    renderPage()
    const generate = await screen.findByRole('button', { name: '生成新候選' })
    expect(generate).toBeEnabled()
  })

  it('walks a checkpoint from queued → running → completed via SSE', async () => {
    getCreationSessionMock.mockResolvedValue(makeSessionDetail([]))
    createCheckpointMock.mockResolvedValue({
      task_id: 'task-1',
      checkpoint_id: 'cp-new',
    } satisfies CreateCheckpointResponse)
    renderPage()

    // Wait for the page to settle.
    await screen.findByRole('button', { name: '生成新候選' })

    fireEvent.click(screen.getByRole('button', { name: '生成新候選' }))

    // Placeholder appears on submit success (queued state).
    const card = await screen.findByTestId('checkpoint-card-cp-new')
    expect(card).toHaveAttribute('data-status', 'queued')

    pushSse('task-1', { status: 'running', progress: 0.4 })
    await waitFor(() => expect(card).toHaveAttribute('data-status', 'running'))

    pushSse('task-1', {
      status: 'completed',
      result: {
        checkpoint: makeCheckpoint({
          id: 'cp-new',
          sequence: 7,
          thumbnail_url: 'https://img/new-thumb.png',
        }),
      },
    })
    await waitFor(() => expect(card).toHaveAttribute('data-status', 'completed'))
    expect(within(card).getByText('#7')).toBeInTheDocument()
    expect(within(card).getByRole('img')).toHaveAttribute('src', 'https://img/new-thumb.png')
  })

  it('keeps three concurrent SSE streams independent', async () => {
    getCreationSessionMock.mockResolvedValue(makeSessionDetail([]))
    createCheckpointMock
      .mockResolvedValueOnce({ task_id: 'task-A', checkpoint_id: 'cp-A' })
      .mockResolvedValueOnce({ task_id: 'task-B', checkpoint_id: 'cp-B' })
      .mockResolvedValueOnce({ task_id: 'task-C', checkpoint_id: 'cp-C' })
    renderPage()
    await screen.findByRole('button', { name: '生成新候選' })

    const generate = screen.getByRole('button', { name: '生成新候選' })
    fireEvent.click(generate)
    await screen.findByTestId('checkpoint-card-cp-A')
    fireEvent.click(generate)
    await screen.findByTestId('checkpoint-card-cp-B')
    fireEvent.click(generate)
    await screen.findByTestId('checkpoint-card-cp-C')

    // Move A and C to running, leave B queued.
    pushSse('task-A', { status: 'running', progress: 0.5 })
    pushSse('task-C', { status: 'running', progress: 0.1 })

    await waitFor(() => {
      expect(screen.getByTestId('checkpoint-card-cp-A')).toHaveAttribute('data-status', 'running')
    })
    expect(screen.getByTestId('checkpoint-card-cp-B')).toHaveAttribute('data-status', 'queued')
    expect(screen.getByTestId('checkpoint-card-cp-C')).toHaveAttribute('data-status', 'running')
  })

  it('用這張再改 prefills the form with the placeholder request and shows the remix header', async () => {
    getCreationSessionMock.mockResolvedValue(makeSessionDetail([]))
    createCheckpointMock.mockResolvedValue({ task_id: 'task-r', checkpoint_id: 'cp-r' })
    renderPage()
    await screen.findByRole('button', { name: '生成新候選' })

    // Submit so we have a placeholder with stored inputs.
    fireEvent.change(screen.getByLabelText('自由補述'), {
      target: { value: '溫柔氣質' },
    })
    fireEvent.click(screen.getByRole('button', { name: '生成新候選' }))
    await screen.findByTestId('checkpoint-card-cp-r')

    pushSse('task-r', {
      status: 'completed',
      result: { checkpoint: makeCheckpoint({ id: 'cp-r', sequence: 2 }) },
    })

    await waitFor(() =>
      expect(screen.getByTestId('checkpoint-card-cp-r')).toHaveAttribute(
        'data-status',
        'completed',
      ),
    )

    fireEvent.click(
      within(screen.getByTestId('checkpoint-card-cp-r')).getByRole('button', {
        name: '用這張再改',
      }),
    )

    // Header switches; freeform stays prefilled.
    expect(await screen.findByTestId('remix-context-header')).toHaveTextContent('基於 Ckpt #2')
    expect(screen.getByLabelText('自由補述')).toHaveValue('溫柔氣質')
  })

  it('從頭 clears the form and resets the remix context', async () => {
    getCreationSessionMock.mockResolvedValue(makeSessionDetail([]))
    createCheckpointMock.mockResolvedValue({ task_id: 'task-x', checkpoint_id: 'cp-x' })
    renderPage()
    await screen.findByRole('button', { name: '生成新候選' })

    fireEvent.change(screen.getByLabelText('自由補述'), {
      target: { value: '某些字' },
    })
    fireEvent.click(screen.getByRole('button', { name: '生成新候選' }))
    await screen.findByTestId('checkpoint-card-cp-x')
    pushSse('task-x', {
      status: 'completed',
      result: { checkpoint: makeCheckpoint({ id: 'cp-x', sequence: 1 }) },
    })
    await waitFor(() =>
      expect(screen.getByTestId('checkpoint-card-cp-x')).toHaveAttribute(
        'data-status',
        'completed',
      ),
    )

    fireEvent.click(
      within(screen.getByTestId('checkpoint-card-cp-x')).getByRole('button', {
        name: '用這張再改',
      }),
    )
    expect(await screen.findByTestId('remix-context-header')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '從頭' }))
    expect(screen.queryByTestId('remix-context-header')).not.toBeInTheDocument()
    expect(screen.getByLabelText('自由補述')).toHaveValue('')
  })

  it('shows the failed error message and lets the user retry', async () => {
    getCreationSessionMock.mockResolvedValue(makeSessionDetail([]))
    createCheckpointMock
      .mockResolvedValueOnce({ task_id: 'task-1', checkpoint_id: 'cp-failed' })
      .mockResolvedValueOnce({ task_id: 'task-2', checkpoint_id: 'cp-retry' })
    renderPage()
    await screen.findByRole('button', { name: '生成新候選' })

    fireEvent.click(screen.getByRole('button', { name: '生成新候選' }))
    await screen.findByTestId('checkpoint-card-cp-failed')

    pushSse('task-1', {
      status: 'failed',
      error: {
        code: 'MODEL_TIMEOUT',
        message: '模型逾時',
        retryable: true,
      },
    })

    await waitFor(() =>
      expect(screen.getByTestId('checkpoint-card-cp-failed')).toHaveAttribute(
        'data-status',
        'failed',
      ),
    )
    expect(screen.getByTestId('checkpoint-error-message')).toHaveTextContent('模型逾時')

    const retry = within(screen.getByTestId('checkpoint-card-cp-failed')).getByRole('button', {
      name: '重試',
    })
    fireEvent.click(retry)

    await screen.findByTestId('checkpoint-card-cp-retry')
    expect(createCheckpointMock).toHaveBeenCalledTimes(2)
  })

  it('cancel_pending shows 取消中… toast and the card flips on the cancel SSE', async () => {
    getCreationSessionMock.mockResolvedValue(makeSessionDetail([]))
    createCheckpointMock.mockResolvedValue({ task_id: 'task-c', checkpoint_id: 'cp-c' })
    cancelTaskMock.mockResolvedValue({
      task: {} as unknown as CancelTaskResponse['task'],
      cancel_outcome: 'cancel_pending',
    })
    renderPage()
    await screen.findByRole('button', { name: '生成新候選' })

    fireEvent.click(screen.getByRole('button', { name: '生成新候選' }))
    await screen.findByTestId('checkpoint-card-cp-c')
    pushSse('task-c', { status: 'running', progress: 0.2 })

    const card = screen.getByTestId('checkpoint-card-cp-c')
    fireEvent.click(within(card).getByRole('button', { name: /取消/ }))

    await waitFor(() => expect(cancelTaskMock).toHaveBeenCalledWith('task-c'))
    await waitFor(() => expect(sonnerCalls.find((c) => c.kind === 'info')?.message).toBe('取消中…'))

    pushSse('task-c', { status: 'cancelled' })
    await waitFor(() => expect(card).toHaveAttribute('data-status', 'cancelled'))
  })

  it('cancel_outcome cancelled_immediately settles the card synchronously without SSE', async () => {
    getCreationSessionMock.mockResolvedValue(makeSessionDetail([]))
    createCheckpointMock.mockResolvedValue({ task_id: 'task-q', checkpoint_id: 'cp-q' })
    cancelTaskMock.mockResolvedValue({
      task: {} as unknown as CancelTaskResponse['task'],
      cancel_outcome: 'cancelled_immediately',
    })
    renderPage()
    await screen.findByRole('button', { name: '生成新候選' })

    fireEvent.click(screen.getByRole('button', { name: '生成新候選' }))
    const card = await screen.findByTestId('checkpoint-card-cp-q')
    expect(card).toHaveAttribute('data-status', 'queued')

    fireEvent.click(within(card).getByRole('button', { name: /取消/ }))

    // No SSE event is pushed — the card should flip on the mutation success
    // alone (api-shape §5.5: queued task removed from queue on this outcome).
    await waitFor(() => expect(card).toHaveAttribute('data-status', 'cancelled'))
    expect(sonnerCalls.find((c) => c.kind === 'success')?.message).toBe('已取消')
  })

  it('rolls back the optimistic cancel flag when the cancel mutation fails', async () => {
    getCreationSessionMock.mockResolvedValue(makeSessionDetail([]))
    createCheckpointMock.mockResolvedValue({ task_id: 'task-err', checkpoint_id: 'cp-err' })
    cancelTaskMock.mockRejectedValue(new Error('network'))
    renderPage()
    await screen.findByRole('button', { name: '生成新候選' })

    fireEvent.click(screen.getByRole('button', { name: '生成新候選' }))
    await screen.findByTestId('checkpoint-card-cp-err')
    pushSse('task-err', { status: 'running', progress: 0.3 })

    const card = screen.getByTestId('checkpoint-card-cp-err')
    fireEvent.click(within(card).getByRole('button', { name: /取消/ }))

    // After the mutation rejects the cancel button text reverts to "取消".
    await waitFor(() => {
      const btn = within(card).getByRole('button', { name: /取消/ })
      expect(btn).toHaveTextContent(/^取消$/)
      expect(btn).toBeEnabled()
    })
  })

  it('用同設定再試一次 is disabled until a completed checkpoint exists', async () => {
    getCreationSessionMock.mockResolvedValue(makeSessionDetail([]))
    createCheckpointMock
      .mockResolvedValueOnce({ task_id: 'task-r1', checkpoint_id: 'cp-r1' })
      .mockResolvedValueOnce({ task_id: 'task-r2', checkpoint_id: 'cp-r2' })
    renderPage()
    await screen.findByRole('button', { name: '生成新候選' })

    const retryBtn = screen.getByRole('button', { name: '用同設定再試一次' })
    expect(retryBtn).toBeDisabled()

    fireEvent.click(screen.getByRole('button', { name: '生成新候選' }))
    await screen.findByTestId('checkpoint-card-cp-r1')
    pushSse('task-r1', {
      status: 'completed',
      result: { checkpoint: makeCheckpoint({ id: 'cp-r1', sequence: 1 }) },
    })
    await waitFor(() => expect(retryBtn).toBeEnabled())

    fireEvent.click(retryBtn)
    await waitFor(() => expect(createCheckpointMock).toHaveBeenCalledTimes(2))
    const lastCall = createCheckpointMock.mock.calls.at(-1)?.[1]
    expect(lastCall?.mode).toBe('retry_same')
    expect(lastCall?.base_checkpoint_id).toBe('cp-r1')
  })

  it('non-2xx SSE response surfaces as a failed card with toast', async () => {
    getCreationSessionMock.mockResolvedValue(makeSessionDetail([]))
    createCheckpointMock.mockResolvedValue({
      task_id: 'task-401',
      checkpoint_id: 'cp-401',
    })
    renderPage()
    await screen.findByRole('button', { name: '生成新候選' })

    fireEvent.click(screen.getByRole('button', { name: '生成新候選' }))
    const card = await screen.findByTestId('checkpoint-card-cp-401')

    // Drive the synthetic failure path the hook produces when `onopen` rejects
    // a non-event-stream response.
    pushSse('task-401', {
      status: 'failed',
      error: { code: 'SSE_ABORTED', message: '連線中斷', retryable: true },
    })

    await waitFor(() => expect(card).toHaveAttribute('data-status', 'failed'))
    expect(within(card).getByTestId('checkpoint-error-message')).toHaveTextContent('連線中斷')
    expect(sonnerCalls.find((c) => c.kind === 'error')?.message).toBe('連線中斷')
  })

  it('too_late_completed hydrates checkpoint from task.result so the card lands on the final image', async () => {
    getCreationSessionMock.mockResolvedValue(makeSessionDetail([]))
    createCheckpointMock.mockResolvedValue({ task_id: 'task-tlc', checkpoint_id: 'cp-tlc' })
    const finalCheckpoint = makeCheckpoint({
      id: 'cp-tlc',
      sequence: 9,
      thumbnail_url: 'https://img/tlc-thumb.png',
    })
    cancelTaskMock.mockResolvedValue({
      task: {
        id: 'task-tlc',
        status: 'completed',
        result: { checkpoint: finalCheckpoint },
      } as unknown as CancelTaskResponse['task'],
      cancel_outcome: 'too_late_completed',
    })
    renderPage()
    await screen.findByRole('button', { name: '生成新候選' })

    fireEvent.click(screen.getByRole('button', { name: '生成新候選' }))
    const card = await screen.findByTestId('checkpoint-card-cp-tlc')
    pushSse('task-tlc', { status: 'running', progress: 0.9 })

    fireEvent.click(within(card).getByRole('button', { name: /取消/ }))

    // No trailing SSE pushed — the card must still land on the completed
    // image purely from the cancel mutation's task payload.
    await waitFor(() => expect(card).toHaveAttribute('data-status', 'completed'))
    expect(within(card).getByText('#9')).toBeInTheDocument()
    expect(within(card).getByRole('img')).toHaveAttribute('src', 'https://img/tlc-thumb.png')
  })

  it('cancel_outcome too_late_completed surfaces 來不及取消 toast', async () => {
    getCreationSessionMock.mockResolvedValue(makeSessionDetail([]))
    createCheckpointMock.mockResolvedValue({ task_id: 'task-late', checkpoint_id: 'cp-late' })
    cancelTaskMock.mockResolvedValue({
      task: {} as unknown as CancelTaskResponse['task'],
      cancel_outcome: 'too_late_completed',
    })
    renderPage()
    await screen.findByRole('button', { name: '生成新候選' })

    fireEvent.click(screen.getByRole('button', { name: '生成新候選' }))
    await screen.findByTestId('checkpoint-card-cp-late')
    pushSse('task-late', { status: 'running' })

    fireEvent.click(
      within(screen.getByTestId('checkpoint-card-cp-late')).getByRole('button', { name: /取消/ }),
    )

    await waitFor(() =>
      expect(sonnerCalls.find((c) => c.kind === 'warning')?.message).toBe('來不及取消'),
    )
  })

  it('進階檢視 fires a placeholder toast (T-024 owner)', async () => {
    getCreationSessionMock.mockResolvedValue(makeSessionDetail([]))
    renderPage()
    await screen.findByRole('button', { name: '生成新候選' })

    fireEvent.click(screen.getByRole('button', { name: '進階檢視 Prompt' }))

    expect(sonnerCalls.find((c) => c.kind === 'info')?.message).toMatch(/T-024/)
  })

  it('completed checkpoint with null thumbnail still exposes the lightbox via output_image_url', async () => {
    getCreationSessionMock.mockResolvedValue(
      makeSessionDetail([
        makeCheckpoint({
          id: 'cp-noThumb',
          sequence: 4,
          thumbnail_url: null,
          output_image_url: 'https://img/full-only.png',
        }),
      ]),
    )
    renderPage()
    const card = await screen.findByTestId('checkpoint-card-cp-noThumb')

    // Card should still render the lightbox button (using output_image_url
    // as the visible image) instead of falling through to the spinner.
    expect(card).toHaveAttribute('data-status', 'completed')
    const openBtn = within(card).getByRole('button', { name: /Checkpoint #4/ })
    expect(openBtn).toBeInTheDocument()
    expect(within(openBtn).getByRole('img')).toHaveAttribute('src', 'https://img/full-only.png')
  })

  it('opens the lightbox with the checkpoint prompt summary on thumbnail click', async () => {
    getCreationSessionMock.mockResolvedValue(
      makeSessionDetail([
        makeCheckpoint({
          id: 'cp-light',
          sequence: 3,
          prompt_summary: '紅旗袍版本',
        }),
      ]),
    )
    renderPage()
    const card = await screen.findByTestId('checkpoint-card-cp-light')

    fireEvent.click(within(card).getByRole('button', { name: /Checkpoint #3/ }))

    expect(await screen.findByTestId('checkpoint-lightbox')).toBeInTheDocument()
    expect(screen.getByText('紅旗袍版本')).toBeInTheDocument()
  })
})
