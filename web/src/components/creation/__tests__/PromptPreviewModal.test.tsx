import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { PromptPreviewModal } from '../PromptPreviewModal'
import { previewPrompt, type PromptPreviewRequest } from '@/api/endpoints/prompt'
import { ApiError } from '@/api/client'

vi.mock('@/api/endpoints/prompt', async () => {
  const actual =
    await vi.importActual<typeof import('@/api/endpoints/prompt')>('@/api/endpoints/prompt')
  return { ...actual, previewPrompt: vi.fn() }
})

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

const previewPromptMock = vi.mocked(previewPrompt)

const REQUEST: PromptPreviewRequest = {
  mode: 'create_base',
  menu_selections: { gender: 'female', art_style: 'ink_wash' },
  freeform_note: '在森林裡',
  reference_image_ids: null,
}

const HAPPY_RESPONSE = {
  platform_constraints: 'transparent background, centered, facing camera',
  menu_fragments: ['female character', 'ink wash style'],
  reconciled_note_en: 'standing in a forest',
  final_prompt:
    'transparent background, centered, facing camera, female character, ink wash style, standing in a forest',
}

function renderModal(open = true) {
  const onClose = vi.fn()
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false, gcTime: 0 },
      mutations: { retry: false },
    },
  })
  const utils = render(
    <QueryClientProvider client={client}>
      <PromptPreviewModal isOpen={open} onClose={onClose} request={REQUEST} />
    </QueryClientProvider>,
  )
  return { ...utils, onClose, client }
}

beforeEach(() => {
  previewPromptMock.mockReset()
  sonnerCalls.length = 0
  // jsdom doesn't ship a clipboard implementation; install a writable spy.
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText: vi.fn().mockResolvedValue(undefined) },
  })
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('PromptPreviewModal', () => {
  it('does not call the API when closed', () => {
    renderModal(false)
    expect(previewPromptMock).not.toHaveBeenCalled()
  })

  it('shows the loading state, then the four sections on success', async () => {
    let resolve: (value: typeof HAPPY_RESPONSE) => void = () => {}
    previewPromptMock.mockImplementation(() => new Promise((r) => (resolve = r)))

    renderModal()

    expect(screen.getByTestId('prompt-preview-loading')).toBeInTheDocument()
    expect(previewPromptMock).toHaveBeenCalledTimes(1)
    expect(previewPromptMock).toHaveBeenCalledWith(REQUEST)

    resolve(HAPPY_RESPONSE)

    expect(await screen.findByText('平台固定 constraints')).toBeInTheDocument()
    expect(screen.getByText('選單片段')).toBeInTheDocument()
    expect(screen.getByText('重寫後的補述（英文）')).toBeInTheDocument()
    expect(screen.getByText('最終 prompt')).toBeInTheDocument()
    expect(screen.getByTestId('prompt-preview-final')).toHaveTextContent(
      HAPPY_RESPONSE.final_prompt,
    )
    const fragments = screen.getByTestId('prompt-preview-menu-fragments')
    expect(fragments).toHaveTextContent('female character')
    expect(fragments).toHaveTextContent('ink wash style')
  })

  it('renders the friendly hint for VALIDATION_EMPTY_INPUT', async () => {
    previewPromptMock.mockRejectedValue(
      new ApiError(400, 'VALIDATION_EMPTY_INPUT', '請至少提供補述', {
        error: {
          code: 'VALIDATION_EMPTY_INPUT',
          message: '請至少提供補述',
          retryable: false,
        },
      }),
    )

    renderModal()

    expect(await screen.findByTestId('prompt-preview-error-empty')).toHaveTextContent(
      '請先填選項或補述',
    )
  })

  it('renders message + problem + fix for PROMPT_CONFLICT', async () => {
    previewPromptMock.mockRejectedValue(
      new ApiError(400, 'PROMPT_CONFLICT', 'Prompt 衝突', {
        error: {
          code: 'PROMPT_CONFLICT',
          message: 'Prompt 衝突',
          problem: 'background conflict',
          fix: 'remove background keywords',
          retryable: false,
        },
      }),
    )

    renderModal()

    const alert = await screen.findByTestId('prompt-preview-error')
    expect(alert).toHaveTextContent('Prompt 衝突')
    expect(alert).toHaveTextContent('background conflict')
    expect(alert).toHaveTextContent('remove background keywords')
  })

  it('copies the final prompt and toasts on success', async () => {
    previewPromptMock.mockResolvedValue(HAPPY_RESPONSE)
    renderModal()

    const copyBtn = await screen.findByTestId('prompt-preview-copy')
    fireEvent.click(copyBtn)

    await waitFor(() =>
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith(HAPPY_RESPONSE.final_prompt),
    )
    await waitFor(() => expect(sonnerCalls).toContainEqual({ kind: 'success', message: '已複製' }))
  })

  it('invokes onClose when the dialog is dismissed', async () => {
    previewPromptMock.mockResolvedValue(HAPPY_RESPONSE)
    const { onClose } = renderModal()

    await screen.findByText('最終 prompt')
    fireEvent.keyDown(document.body, { key: 'Escape' })

    await waitFor(() => expect(onClose).toHaveBeenCalled())
  })

  it('re-fetches when the modal closes and reopens', async () => {
    previewPromptMock.mockResolvedValue(HAPPY_RESPONSE)
    const onClose = vi.fn()
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false, refetchOnWindowFocus: false, gcTime: 0 },
        mutations: { retry: false },
      },
    })
    const { rerender } = render(
      <QueryClientProvider client={client}>
        <PromptPreviewModal isOpen onClose={onClose} request={REQUEST} />
      </QueryClientProvider>,
    )
    await screen.findByText('最終 prompt')
    expect(previewPromptMock).toHaveBeenCalledTimes(1)

    rerender(
      <QueryClientProvider client={client}>
        <PromptPreviewModal isOpen={false} onClose={onClose} request={REQUEST} />
      </QueryClientProvider>,
    )
    rerender(
      <QueryClientProvider client={client}>
        <PromptPreviewModal isOpen onClose={onClose} request={REQUEST} />
      </QueryClientProvider>,
    )

    // Radix unmounts DialogContent on close, so reopening mounts a fresh
    // useQuery — the ticket requires this so the user sees the latest
    // reconciler output (backend Redis caches duplicate work).
    await waitFor(() => expect(previewPromptMock).toHaveBeenCalledTimes(2))
  })

  it('renders the unsupported-reason notice and skips the API call', async () => {
    const onClose = vi.fn()
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false, refetchOnWindowFocus: false, gcTime: 0 },
        mutations: { retry: false },
      },
    })
    render(
      <QueryClientProvider client={client}>
        <PromptPreviewModal
          isOpen
          onClose={onClose}
          request={REQUEST}
          unsupportedReason="進階檢視 暫不支援 remix 模式"
        />
      </QueryClientProvider>,
    )

    expect(await screen.findByTestId('prompt-preview-unsupported')).toHaveTextContent(
      '進階檢視 暫不支援 remix 模式',
    )
    expect(previewPromptMock).not.toHaveBeenCalled()
  })

  it('toasts an error when clipboard write fails', async () => {
    previewPromptMock.mockResolvedValue(HAPPY_RESPONSE)
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: vi.fn().mockRejectedValue(new Error('blocked')) },
    })
    renderModal()

    const copyBtn = await screen.findByTestId('prompt-preview-copy')
    fireEvent.click(copyBtn)

    await waitFor(() => expect(sonnerCalls).toContainEqual({ kind: 'error', message: '複製失敗' }))
  })
})
