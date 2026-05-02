import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/api/client'
import {
  createMotion,
  listAliasMotions,
  listBaseMotions,
  type CreateMotionResponse,
  type MotionListResponse,
  type MotionParentRef,
} from '@/api/endpoints/motions'
import { TooltipProvider } from '@/components/ui/tooltip'
import { useAuthStore } from '@/stores/authStore'

import { CustomMotionModal } from '../CustomMotionModal'
import { MotionRow } from '../MotionRow'

vi.mock('@/api/endpoints/motions', async () => {
  const actual =
    await vi.importActual<typeof import('@/api/endpoints/motions')>('@/api/endpoints/motions')
  return {
    ...actual,
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
const listBaseMotionsMock = vi.mocked(listBaseMotions)
const listAliasMotionsMock = vi.mocked(listAliasMotions)

const ME_ID = '11111111-1111-1111-1111-111111111111'
const BASE_ID = 'bbbbbbbb-0000-0000-0000-000000000222'
const PARENT: MotionParentRef = { type: 'base', id: BASE_ID }

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false, gcTime: 0 },
      mutations: { retry: false },
    },
  })
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

interface RenderModalOptions {
  isOpen?: boolean
  onClose?: () => void
  onSuccess?: (response: CreateMotionResponse, name: string, description: string) => void
}

function renderModal(opts: RenderModalOptions = {}) {
  const onClose = opts.onClose ?? vi.fn()
  const onSuccess = opts.onSuccess ?? vi.fn()
  const utils = render(
    <QueryClientProvider client={makeQueryClient()}>
      <TooltipProvider>
        <CustomMotionModal
          isOpen={opts.isOpen ?? true}
          parent={PARENT}
          onClose={onClose}
          onSuccess={onSuccess}
        />
      </TooltipProvider>
    </QueryClientProvider>,
  )
  return { ...utils, onClose, onSuccess }
}

function fillForm({ name, description }: { name?: string; description?: string }) {
  if (name !== undefined) {
    fireEvent.change(screen.getByTestId('custom-motion-name-input'), {
      target: { value: name },
    })
  }
  if (description !== undefined) {
    fireEvent.change(screen.getByTestId('custom-motion-description-input'), {
      target: { value: description },
    })
  }
}

describe('CustomMotionModal', () => {
  beforeEach(() => {
    seedAuth()
    sseHandlers.clear()
    sonnerCalls.length = 0
    createMotionMock.mockReset()
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

  it('disables [生成] until both name and description have content', () => {
    renderModal()
    const submit = screen.getByTestId('custom-motion-submit')
    expect(submit).toBeDisabled()

    fillForm({ name: '轉身揮手' })
    expect(submit).toBeDisabled()

    fillForm({ description: '慢慢轉身 180 度，轉到背對鏡頭後再回頭揮手' })
    expect(submit).not.toBeDisabled()
  })

  it('disables [生成] when name exceeds the 50-char limit', () => {
    renderModal()
    const longName = '名'.repeat(51)
    fillForm({ name: longName, description: '描述' })
    expect(screen.getByTestId('custom-motion-submit')).toBeDisabled()
    // The counter flips to destructive styling so the user can see why.
    expect(screen.getByTestId('custom-motion-name-counter')).toHaveTextContent('51/50')
    expect(screen.getByTestId('custom-motion-name-counter').className).toContain('destructive')
  })

  it('disables [生成] when description exceeds the 500-char limit', () => {
    renderModal()
    const longDescription = '描'.repeat(501)
    fillForm({ name: '揮手', description: longDescription })
    expect(screen.getByTestId('custom-motion-submit')).toBeDisabled()
    expect(screen.getByTestId('custom-motion-description-counter')).toHaveTextContent('501/500')
  })

  it('submits the trimmed payload and fires onSuccess on 200', async () => {
    createMotionMock.mockResolvedValue({
      task_id: 'task-1',
      motion_id: 'motion-1',
    } satisfies CreateMotionResponse)
    const onSuccess = vi.fn()
    const onClose = vi.fn()
    renderModal({ onClose, onSuccess })

    fillForm({ name: '  轉身揮手  ', description: '  慢慢轉身 180 度，然後揮手  ' })
    fireEvent.click(screen.getByTestId('custom-motion-submit'))

    await waitFor(() =>
      expect(createMotionMock).toHaveBeenCalledWith(PARENT, {
        motion_type: 'custom',
        name: '轉身揮手',
        description: '慢慢轉身 180 度，然後揮手',
      }),
    )
    await waitFor(() =>
      expect(onSuccess).toHaveBeenCalledWith(
        { task_id: 'task-1', motion_id: 'motion-1' },
        '轉身揮手',
        '慢慢轉身 180 度，然後揮手',
      ),
    )
    // Modal close belongs to the caller — it isn't fired internally on
    // success so caller-controlled close timing stays explicit.
    expect(onClose).not.toHaveBeenCalled()
  })

  it('keeps the modal open and surfaces an inline error on CONFLICT_DUPLICATE_NAME', async () => {
    createMotionMock.mockRejectedValue(
      new ApiError(409, 'CONFLICT_DUPLICATE_NAME', '名稱已存在', {
        error: { code: 'CONFLICT_DUPLICATE_NAME', message: '名稱已存在' },
      }),
    )
    const onSuccess = vi.fn()
    const onClose = vi.fn()
    renderModal({ onClose, onSuccess })

    fillForm({ name: '轉身揮手', description: '慢慢轉身' })
    fireEvent.click(screen.getByTestId('custom-motion-submit'))

    expect(await screen.findByTestId('custom-motion-error')).toHaveTextContent(
      '此 motion 名稱已被使用',
    )
    expect(onSuccess).not.toHaveBeenCalled()
    expect(onClose).not.toHaveBeenCalled()
    // Modal is still mounted — check the title is visible.
    expect(screen.getByText('新增自訂 Motion')).toBeInTheDocument()
  })

  it('surfaces VALIDATION_* inline using the backend message', async () => {
    createMotionMock.mockRejectedValue(
      new ApiError(400, 'VALIDATION_INVALID_CHARS', '描述含有不允許的字元', {
        error: { code: 'VALIDATION_INVALID_CHARS', message: '描述含有不允許的字元' },
      }),
    )
    renderModal()
    fillForm({ name: '揮手', description: '<bad>' })
    fireEvent.click(screen.getByTestId('custom-motion-submit'))
    expect(await screen.findByTestId('custom-motion-error')).toHaveTextContent(
      '描述含有不允許的字元',
    )
  })

  it('toasts non-form errors instead of swallowing them', async () => {
    createMotionMock.mockRejectedValue(
      new ApiError(502, 'MODEL_UNAVAILABLE', 'Veo 暫時不可用', {
        error: { code: 'MODEL_UNAVAILABLE', message: 'Veo 暫時不可用' },
      }),
    )
    renderModal()
    fillForm({ name: '揮手', description: '描述' })
    fireEvent.click(screen.getByTestId('custom-motion-submit'))

    await waitFor(() =>
      expect(sonnerCalls.find((c) => c.kind === 'error')?.message).toBe('Veo 暫時不可用'),
    )
    // Inline error mirrors the toast so the user sees the failure even
    // if the toast already faded.
    expect(screen.getByTestId('custom-motion-error')).toHaveTextContent('Veo 暫時不可用')
  })

  it('cancel button fires onClose without submitting', () => {
    const onClose = vi.fn()
    renderModal({ onClose })
    fillForm({ name: '揮手', description: '描述' })
    fireEvent.click(screen.getByText('取消'))
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(createMotionMock).not.toHaveBeenCalled()
  })

  it('resets the form whenever the modal reopens', () => {
    const { rerender } = renderModal({ isOpen: true })
    fillForm({ name: '舊名稱', description: '舊描述' })
    expect(screen.getByTestId('custom-motion-name-input')).toHaveValue('舊名稱')

    rerender(
      <QueryClientProvider client={makeQueryClient()}>
        <TooltipProvider>
          <CustomMotionModal
            isOpen={false}
            parent={PARENT}
            onClose={() => {}}
            onSuccess={() => {}}
          />
        </TooltipProvider>
      </QueryClientProvider>,
    )
    rerender(
      <QueryClientProvider client={makeQueryClient()}>
        <TooltipProvider>
          <CustomMotionModal isOpen parent={PARENT} onClose={() => {}} onSuccess={() => {}} />
        </TooltipProvider>
      </QueryClientProvider>,
    )

    expect(screen.getByTestId('custom-motion-name-input')).toHaveValue('')
    expect(screen.getByTestId('custom-motion-description-input')).toHaveValue('')
  })
})

describe('MotionRow + CustomMotionModal integration', () => {
  beforeEach(() => {
    seedAuth()
    sseHandlers.clear()
    sonnerCalls.length = 0
    createMotionMock.mockReset()
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

  it('opens via the [+ 自訂動作] trigger and lands a queued custom cell on success', async () => {
    createMotionMock.mockResolvedValue({
      task_id: 'task-custom',
      motion_id: 'motion-custom',
    } satisfies CreateMotionResponse)

    render(
      <QueryClientProvider client={makeQueryClient()}>
        <TooltipProvider>
          <MotionRow parentType="base" parentId={BASE_ID} motions={[]} isOwner />
        </TooltipProvider>
      </QueryClientProvider>,
    )

    fireEvent.click(screen.getByTestId(`motion-add-custom-base-${BASE_ID}`))
    expect(await screen.findByTestId('custom-motion-modal')).toBeInTheDocument()

    fireEvent.change(screen.getByTestId('custom-motion-name-input'), {
      target: { value: '轉身揮手' },
    })
    fireEvent.change(screen.getByTestId('custom-motion-description-input'), {
      target: { value: '慢慢轉身 180 度，然後揮手' },
    })
    fireEvent.click(screen.getByTestId('custom-motion-submit'))

    await waitFor(() =>
      expect(createMotionMock).toHaveBeenCalledWith(
        { type: 'base', id: BASE_ID },
        {
          motion_type: 'custom',
          name: '轉身揮手',
          description: '慢慢轉身 180 度，然後揮手',
        },
      ),
    )

    // Modal disappears and the new pending cell shows up keyed by motion_id.
    await waitFor(() => {
      expect(screen.queryByTestId('custom-motion-modal')).not.toBeInTheDocument()
    })
    expect(await screen.findByTestId('motion-cell-queued-motion-custom')).toBeInTheDocument()
  })

  it('keeps the modal open on duplicate-name 409 and surfaces inline error', async () => {
    createMotionMock.mockRejectedValue(
      new ApiError(409, 'CONFLICT_DUPLICATE_NAME', '名稱已存在', {
        error: { code: 'CONFLICT_DUPLICATE_NAME', message: '名稱已存在' },
      }),
    )

    render(
      <QueryClientProvider client={makeQueryClient()}>
        <TooltipProvider>
          <MotionRow parentType="base" parentId={BASE_ID} motions={[]} isOwner />
        </TooltipProvider>
      </QueryClientProvider>,
    )

    fireEvent.click(screen.getByTestId(`motion-add-custom-base-${BASE_ID}`))
    fireEvent.change(screen.getByTestId('custom-motion-name-input'), {
      target: { value: '揮手' },
    })
    fireEvent.change(screen.getByTestId('custom-motion-description-input'), {
      target: { value: '描述' },
    })
    fireEvent.click(screen.getByTestId('custom-motion-submit'))

    expect(await screen.findByTestId('custom-motion-error')).toHaveTextContent(
      '此 motion 名稱已被使用',
    )
    expect(screen.getByTestId('custom-motion-modal')).toBeInTheDocument()
  })

  it('non-owner sees the disabled trigger with the owner-only tooltip wrapper', () => {
    render(
      <QueryClientProvider client={makeQueryClient()}>
        <TooltipProvider>
          <MotionRow parentType="base" parentId={BASE_ID} motions={[]} isOwner={false} />
        </TooltipProvider>
      </QueryClientProvider>,
    )
    const button = screen.getByTestId(`motion-add-custom-base-${BASE_ID}`)
    expect(button).toBeDisabled()
    fireEvent.click(button)
    expect(createMotionMock).not.toHaveBeenCalled()
  })
})
