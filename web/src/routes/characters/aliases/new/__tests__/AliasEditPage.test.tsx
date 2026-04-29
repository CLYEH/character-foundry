import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import AliasEditPage from '../AliasEditPage'
import { ApiError } from '@/api/client'
import {
  createAlias,
  uploadCharacterReference,
  uploadMask,
  type CreateAliasResponse,
  type UploadMaskResponse,
} from '@/api/endpoints/aliases'
import { getCharacter, type CharacterDetail } from '@/api/endpoints/characters'
import { cancelTask, type CancelTaskResponse, type TaskEvent } from '@/api/endpoints/tasks'
import { TooltipProvider } from '@/components/ui/tooltip'
import { useAuthStore } from '@/stores/authStore'

// ---------------------------------------------------------------------------
// Endpoint mocks
// ---------------------------------------------------------------------------

vi.mock('@/api/endpoints/aliases', async () => {
  const actual =
    await vi.importActual<typeof import('@/api/endpoints/aliases')>('@/api/endpoints/aliases')
  return {
    ...actual,
    createAlias: vi.fn(),
    uploadMask: vi.fn(),
    uploadCharacterReference: vi.fn(),
  }
})

vi.mock('@/api/endpoints/characters', async () => {
  const actual = await vi.importActual<typeof import('@/api/endpoints/characters')>(
    '@/api/endpoints/characters',
  )
  return { ...actual, getCharacter: vi.fn() }
})

vi.mock('@/api/endpoints/tasks', async () => {
  const actual =
    await vi.importActual<typeof import('@/api/endpoints/tasks')>('@/api/endpoints/tasks')
  return { ...actual, cancelTask: vi.fn() }
})

// PromptPreviewModal fires a real `previewPrompt` query when opened. We
// only need to assert that opening works; stub the network call so the
// modal's pending-state UI is the visible end state in this suite.
vi.mock('@/api/endpoints/prompt', async () => {
  const actual =
    await vi.importActual<typeof import('@/api/endpoints/prompt')>('@/api/endpoints/prompt')
  return { ...actual, previewPrompt: vi.fn(() => new Promise(() => {})) }
})

// react-konva can't render in jsdom (no real canvas), and the page-level
// flow only needs the mask payload to be observable. The fake `<button>`
// lets tests synthesise a mask without driving the canvas, mirroring the
// real `onMaskChange` callback signature exactly.
vi.mock('@/components/aliases/InpaintCanvas', async () => {
  const React = await import('react')
  const InpaintCanvas = ({
    onMaskChange,
    enabled,
  }: {
    onMaskChange: (mask: { blob: Blob; coveragePercent: number } | null) => void
    enabled: boolean
  }) => {
    return React.createElement(
      'div',
      { 'data-testid': 'inpaint-canvas-fake' },
      React.createElement(
        'button',
        {
          type: 'button',
          'data-testid': 'fake-paint-mask',
          disabled: !enabled,
          onClick: () =>
            onMaskChange({
              blob: new Blob(['mask-bytes'], { type: 'image/png' }),
              coveragePercent: 12.34,
            }),
        },
        'paint',
      ),
      React.createElement(
        'button',
        {
          type: 'button',
          'data-testid': 'fake-clear-mask',
          disabled: !enabled,
          onClick: () => onMaskChange(null),
        },
        'clear',
      ),
    )
  }
  return { InpaintCanvas }
})

// SSE: identical pattern to CreationSessionPage tests — capture the
// `onmessage` handler keyed by URL so tests can `pushSse` synthetic events.
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

// ---------------------------------------------------------------------------
// Fixture helpers
// ---------------------------------------------------------------------------

const ME_ID = '11111111-1111-1111-1111-111111111111'
const CHARACTER_ID = 'aaaaaaaa-0000-0000-0000-000000000111'

const createAliasMock = vi.mocked(createAlias)
const uploadMaskMock = vi.mocked(uploadMask)
const uploadReferenceMock = vi.mocked(uploadCharacterReference)
const getCharacterMock = vi.mocked(getCharacter)
const cancelTaskMock = vi.mocked(cancelTask)

function makeDetail(overrides: Partial<CharacterDetail> = {}): CharacterDetail {
  return {
    id: CHARACTER_ID,
    name: '小雅',
    slug: 'xiao-ya',
    owner: { id: ME_ID, name: 'Leo' },
    base: {
      id: 'base-1',
      character_id: CHARACTER_ID,
      image_url: 'https://img/base.png',
      thumbnail_url: 'https://img/base-thumb.png',
      from_checkpoint_id: 'cp-1',
      created_at: '2026-04-28T10:00:00Z',
    },
    aliases: [],
    motions_summary: { base: { preset_generated: 0, custom_count: 0 }, aliases: [] },
    creation_session: null,
    copied_from: null,
    created_at: '2026-04-28T08:15:00Z',
    updated_at: '2026-04-28T10:00:00Z',
    ...overrides,
  }
}

function pushSse(taskId: string, event: TaskEvent) {
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
        <MemoryRouter initialEntries={[`/characters/${CHARACTER_ID}/aliases/new`]}>
          <Routes>
            <Route
              path="/characters/:id"
              element={<div data-testid="character-detail-stub">detail</div>}
            />
            <Route path="/characters/:id/aliases/new" element={<AliasEditPage />} />
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
  createAliasMock.mockReset()
  uploadMaskMock.mockReset()
  uploadReferenceMock.mockReset()
  getCharacterMock.mockReset()
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

describe('AliasEditPage', () => {
  it('shows the inline no-base error when the character has no Base yet', async () => {
    getCharacterMock.mockResolvedValue({ character: makeDetail({ base: null }) })
    renderPage()
    expect(await screen.findByTestId('alias-edit-no-base')).toBeInTheDocument()
  })

  it('renders the base image and disables 生成 until an input has content', async () => {
    getCharacterMock.mockResolvedValue({ character: makeDetail() })
    renderPage()
    expect(await screen.findByTestId('alias-base-image')).toHaveAttribute(
      'src',
      'https://img/base.png',
    )

    const submit = screen.getByTestId('alias-submit')
    expect(submit).toBeDisabled()
    expect(screen.getByTestId('alias-submit-hint')).toHaveTextContent('請先填 Alias 名稱')

    // Filling the name alone is still not enough — at least one input
    // section needs content per acceptance.
    fireEvent.change(screen.getByLabelText('Alias 名稱'), { target: { value: '紅旗袍' } })
    expect(submit).toBeDisabled()
    expect(screen.getByTestId('alias-submit-hint')).toHaveTextContent('至少要填一項')
  })

  it('text-only happy path: submit → SSE complete → toast + nav back', async () => {
    // Two getCharacter calls expected: initial mount + post-completion
    // refetch (triggered by invalidation in the SSE `completed` branch).
    // Codex P2 round 3: invalidation must fire on terminal completion,
    // not on POST success — otherwise the cached pre-alias snapshot
    // serves stale-fresh for staleTime.
    getCharacterMock.mockResolvedValue({ character: makeDetail() })
    createAliasMock.mockResolvedValue({
      task_id: 'task-1',
      alias_id: 'alias-new',
    } satisfies CreateAliasResponse)
    renderPage()

    await screen.findByTestId('alias-base-image')
    fireEvent.change(screen.getByLabelText('Alias 名稱'), { target: { value: '紅旗袍' } })
    fireEvent.change(screen.getByLabelText('Alias 補述內容'), { target: { value: '換成紅色旗袍' } })

    const submit = screen.getByTestId('alias-submit')
    await waitFor(() => expect(submit).toBeEnabled())
    fireEvent.click(submit)

    await waitFor(() => expect(createAliasMock).toHaveBeenCalledTimes(1))
    // POST resolved but SSE hasn't fired yet — getCharacter must NOT
    // have been re-fetched (invalidation is deferred to completion).
    expect(getCharacterMock).toHaveBeenCalledTimes(1)
    const body = createAliasMock.mock.calls[0][1]
    expect(body).toMatchObject({
      name: '紅旗袍',
      input_mode: 'text',
      freeform_note: '換成紅色旗袍',
      reference_image_ids: null,
      mask: null,
    })

    pushSse('task-1', {
      status: 'completed',
      result: { checkpoint: undefined } as unknown as TaskEvent['result'],
    })

    await waitFor(() => {
      expect(screen.getByTestId('character-detail-stub')).toBeInTheDocument()
    })
    expect(sonnerCalls.find((c) => c.kind === 'success')?.message).toBe('Alias 已建立')
    // SSE completion invalidated the character detail — the second
    // refetch must have fired so the navigation-back lands on fresh data.
    await waitFor(() => expect(getCharacterMock).toHaveBeenCalledTimes(2))
  })

  it('inpaint path uploads the mask and includes mask_id in the alias body', async () => {
    getCharacterMock.mockResolvedValue({ character: makeDetail() })
    uploadMaskMock.mockResolvedValue({ mask_id: 'mask-42' } satisfies UploadMaskResponse)
    createAliasMock.mockResolvedValue({ task_id: 'task-i', alias_id: 'alias-i' })
    renderPage()

    await screen.findByTestId('alias-base-image')
    fireEvent.change(screen.getByLabelText('Alias 名稱'), { target: { value: '面具版' } })

    // Open the inpaint section, then synthesise a mask via the fake canvas.
    fireEvent.click(screen.getByTestId('section-inpaint-toggle'))
    expect(await screen.findByTestId('inpaint-canvas-fake')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('fake-paint-mask'))

    expect(await screen.findByTestId('mask-preview-badge')).toHaveTextContent('Mask 覆蓋 12%')

    const submit = screen.getByTestId('alias-submit')
    await waitFor(() => expect(submit).toBeEnabled())
    fireEvent.click(submit)

    await waitFor(() => expect(uploadMaskMock).toHaveBeenCalledTimes(1))
    expect(uploadMaskMock.mock.calls[0][0]).toBe(CHARACTER_ID)
    expect(uploadMaskMock.mock.calls[0][1]).toBeInstanceOf(Blob)

    await waitFor(() => expect(createAliasMock).toHaveBeenCalledTimes(1))
    const body = createAliasMock.mock.calls[0][1]
    expect(body).toMatchObject({
      input_mode: 'inpaint',
      freeform_note: null,
      reference_image_ids: null,
      mask: { mask_id: 'mask-42' },
    })

    pushSse('task-i', { status: 'completed' })
    await waitFor(() => expect(screen.getByTestId('character-detail-stub')).toBeInTheDocument())
  })

  it('failed task surfaces an error toast and re-enables submit for retry', async () => {
    getCharacterMock.mockResolvedValue({ character: makeDetail() })
    createAliasMock
      .mockResolvedValueOnce({ task_id: 'task-fail', alias_id: 'alias-fail' })
      .mockResolvedValueOnce({ task_id: 'task-retry', alias_id: 'alias-retry' })
    renderPage()

    await screen.findByTestId('alias-base-image')
    fireEvent.change(screen.getByLabelText('Alias 名稱'), { target: { value: '重試版' } })
    fireEvent.change(screen.getByLabelText('Alias 補述內容'), { target: { value: '換髮型' } })

    const submit = screen.getByTestId('alias-submit')
    fireEvent.click(submit)
    await waitFor(() => expect(createAliasMock).toHaveBeenCalledTimes(1))

    pushSse('task-fail', {
      status: 'failed',
      error: { code: 'MODEL_TIMEOUT', message: '模型逾時', retryable: true },
    })

    // Toast surfaced; submit re-enabled (no remount).
    await waitFor(() => {
      expect(sonnerCalls.find((c) => c.kind === 'error')?.message).toBe('模型逾時')
    })
    await waitFor(() => expect(submit).toBeEnabled())

    fireEvent.click(submit)
    await waitFor(() => expect(createAliasMock).toHaveBeenCalledTimes(2))
  })

  it('cancel button: cancelled_immediately settles the page synchronously', async () => {
    getCharacterMock.mockResolvedValue({ character: makeDetail() })
    createAliasMock.mockResolvedValue({ task_id: 'task-c', alias_id: 'alias-c' })
    cancelTaskMock.mockResolvedValue({
      task: {} as unknown as CancelTaskResponse['task'],
      cancel_outcome: 'cancelled_immediately',
    })
    renderPage()

    await screen.findByTestId('alias-base-image')
    fireEvent.change(screen.getByLabelText('Alias 名稱'), { target: { value: '取消版' } })
    fireEvent.change(screen.getByLabelText('Alias 補述內容'), { target: { value: '某些字' } })

    fireEvent.click(screen.getByTestId('alias-submit'))
    await waitFor(() => expect(createAliasMock).toHaveBeenCalledTimes(1))

    fireEvent.click(screen.getByTestId('alias-cancel'))

    await waitFor(() => expect(cancelTaskMock).toHaveBeenCalledWith('task-c'))
    await waitFor(() =>
      expect(sonnerCalls.find((c) => c.kind === 'success')?.message).toBe('已取消'),
    )
    expect(screen.getByTestId('alias-submit')).toBeEnabled()
  })

  it('aborts before alias POST when user unmounts during mask upload', async () => {
    getCharacterMock.mockResolvedValue({ character: makeDetail() })

    // Hold mask upload mid-flight so we can unmount during it. The page
    // must NOT call createAlias once we resume — Codex P1 round 3 says
    // creating the backend task and then cancelling burns quota; we
    // should never create the task in the first place.
    let resolveMask: (value: UploadMaskResponse) => void = () => {}
    uploadMaskMock.mockImplementation(
      () =>
        new Promise<UploadMaskResponse>((resolve) => {
          resolveMask = resolve
        }),
    )

    const { unmount } = renderPage()

    await screen.findByTestId('alias-base-image')
    fireEvent.change(screen.getByLabelText('Alias 名稱'), { target: { value: '中斷版' } })
    fireEvent.click(screen.getByTestId('section-inpaint-toggle'))
    fireEvent.click(await screen.findByTestId('fake-paint-mask'))
    fireEvent.click(screen.getByTestId('alias-submit'))
    await waitFor(() => expect(uploadMaskMock).toHaveBeenCalledTimes(1))

    // Unmount while mask upload is still pending.
    unmount()

    // Backend then completes the mask upload.
    await act(async () => {
      resolveMask({ mask_id: 'mask-stranded' })
      await Promise.resolve()
    })

    // Page detected the unmount before issuing the alias POST — task is
    // never created so no quota gets burned.
    expect(createAliasMock).not.toHaveBeenCalled()
    expect(cancelTaskMock).not.toHaveBeenCalled()
  })

  it('cancels the orphan task when user unmounts mid-POST (after task_id resolves)', async () => {
    getCharacterMock.mockResolvedValue({ character: makeDetail() })
    cancelTaskMock.mockResolvedValue({
      task: {} as unknown as CancelTaskResponse['task'],
      cancel_outcome: 'cancelled_immediately',
    })

    // Hold the createAlias resolution so we can unmount while the POST
    // is still in flight. This is the Codex P1 round 2 scenario: backend
    // accepted the task and minted a task_id, but the user navigated
    // away before the page could subscribe to the SSE stream.
    let resolveCreate: (value: CreateAliasResponse) => void = () => {}
    createAliasMock.mockImplementation(
      () =>
        new Promise<CreateAliasResponse>((resolve) => {
          resolveCreate = resolve
        }),
    )

    const { unmount } = renderPage()

    await screen.findByTestId('alias-base-image')
    fireEvent.change(screen.getByLabelText('Alias 名稱'), { target: { value: '孤兒版' } })
    fireEvent.change(screen.getByLabelText('Alias 補述內容'), { target: { value: '某些字' } })

    fireEvent.click(screen.getByTestId('alias-submit'))
    await waitFor(() => expect(createAliasMock).toHaveBeenCalledTimes(1))

    // Unmount before the POST resolves.
    unmount()

    // Backend now mints task_id and returns it.
    await act(async () => {
      resolveCreate({ task_id: 'task-orphan', alias_id: 'alias-orphan' })
      // Let the awaiting handleSubmit microtask drain.
      await Promise.resolve()
    })

    // The page must have detected the unmount and fired a cancel against
    // the orphaned task to avoid burning quota on an alias the user will
    // never see.
    await waitFor(() => expect(cancelTaskMock).toHaveBeenCalledWith('task-orphan'))
  })

  it('cancel_pending then SSE cancelled emits the success confirmation toast', async () => {
    getCharacterMock.mockResolvedValue({ character: makeDetail() })
    createAliasMock.mockResolvedValue({ task_id: 'task-cp', alias_id: 'alias-cp' })
    cancelTaskMock.mockResolvedValue({
      task: {} as unknown as CancelTaskResponse['task'],
      cancel_outcome: 'cancel_pending',
    })
    renderPage()

    await screen.findByTestId('alias-base-image')
    fireEvent.change(screen.getByLabelText('Alias 名稱'), { target: { value: '取消版' } })
    fireEvent.change(screen.getByLabelText('Alias 補述內容'), { target: { value: '某些字' } })

    fireEvent.click(screen.getByTestId('alias-submit'))
    await waitFor(() => expect(createAliasMock).toHaveBeenCalledTimes(1))

    fireEvent.click(screen.getByTestId('alias-cancel'))
    await waitFor(() => expect(cancelTaskMock).toHaveBeenCalledWith('task-cp'))
    // Interim toast surfaces while the worker tries to abort.
    await waitFor(() => expect(sonnerCalls.find((c) => c.kind === 'info')?.message).toBe('取消中…'))

    // SSE eventually settles to cancelled — the user must get an
    // explicit success toast (Codex P2: silent stop is bad UX).
    pushSse('task-cp', { status: 'cancelled' })
    await waitFor(() =>
      expect(sonnerCalls.find((c) => c.kind === 'success')?.message).toBe('已取消'),
    )
    expect(screen.getByTestId('alias-submit')).toBeEnabled()
  })

  it('renders NotFoundPage on a 404 character lookup', async () => {
    getCharacterMock.mockRejectedValue(
      new ApiError(404, 'NOT_FOUND_CHARACTER', '找不到角色', {
        error: { code: 'NOT_FOUND_CHARACTER', message: '找不到角色' },
      }),
    )
    renderPage()
    expect(await screen.findByTestId('not-found-page')).toBeInTheDocument()
  })

  it('opens the prompt-preview modal in alias mode when 進階檢視 is clicked', async () => {
    getCharacterMock.mockResolvedValue({ character: makeDetail() })
    renderPage()

    await screen.findByTestId('alias-base-image')
    fireEvent.click(screen.getByRole('button', { name: '進階檢視 Prompt' }))

    expect(await screen.findByRole('dialog', { name: '進階檢視 Prompt' })).toBeInTheDocument()
  })

  it('unchecking 標記區域 after painting drops the mask from the submit body', async () => {
    getCharacterMock.mockResolvedValue({ character: makeDetail() })
    createAliasMock.mockResolvedValue({ task_id: 'task-x', alias_id: 'alias-x' })
    renderPage()

    await screen.findByTestId('alias-base-image')
    fireEvent.change(screen.getByLabelText('Alias 名稱'), { target: { value: '只是文字版' } })
    fireEvent.change(screen.getByLabelText('Alias 補述內容'), { target: { value: '換髮' } })

    // Open inpaint, paint a mask, then uncheck — the mask must drop so
    // the alias body never carries a stale mask_id and input_mode falls
    // back to text. (Catches the toBlob race / parent-side gating bug.)
    fireEvent.click(screen.getByTestId('section-inpaint-toggle'))
    fireEvent.click(await screen.findByTestId('fake-paint-mask'))
    expect(await screen.findByTestId('mask-preview-badge')).toHaveTextContent('Mask 覆蓋')

    fireEvent.click(screen.getByTestId('section-inpaint-toggle'))
    // Section collapsed — the badge is no longer rendered.
    await waitFor(() => {
      expect(screen.queryByTestId('mask-preview-badge')).not.toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId('alias-submit'))
    await waitFor(() => expect(createAliasMock).toHaveBeenCalledTimes(1))
    expect(uploadMaskMock).not.toHaveBeenCalled()
    const body = createAliasMock.mock.calls[0][1]
    expect(body).toMatchObject({ input_mode: 'text', mask: null })
  })
})
