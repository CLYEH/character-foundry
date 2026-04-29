import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { PromptPreviewModal } from '../PromptPreviewModal'
import {
  previewPrompt,
  type PromptPreviewAliasRequest,
  type PromptPreviewBaseRequest,
  type PromptPreviewMotionRequest,
  type PromptPreviewRequest,
  type PromptPreviewResponse,
} from '@/api/endpoints/prompt'
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

const BASE_REQUEST: PromptPreviewBaseRequest = {
  mode: 'create_base',
  menu_selections: { gender: 'female', art_style: 'ink_wash' },
  freeform_note: '在森林裡',
  reference_image_ids: null,
}

const REMIX_REQUEST: PromptPreviewBaseRequest = {
  ...BASE_REQUEST,
  base_checkpoint_id: 'cp-7',
}

const ALIAS_REQUEST: PromptPreviewAliasRequest = {
  mode: 'create_alias',
  character_id: 'char-1',
  input_mode: 'mixed',
  freeform_note: '改成紅旗袍',
  reference_image_ids: ['ref-1', 'ref-2'],
  mask: { mask_id: 'mask-9' },
}

const MOTION_PRESET_REQUEST: PromptPreviewMotionRequest = {
  mode: 'create_motion',
  parent_type: 'base',
  parent_id: 'base-1',
  motion_type: 'preset_wave',
  description: null,
}

const MOTION_CUSTOM_REQUEST: PromptPreviewMotionRequest = {
  mode: 'create_motion',
  parent_type: 'alias',
  parent_id: 'alias-1',
  motion_type: 'custom',
  description: '揮手後鞠躬',
}

const HAPPY_BASE_RESPONSE: PromptPreviewResponse = {
  platform_constraints: 'transparent background, centered, facing camera',
  menu_fragments: ['female character', 'ink wash style'],
  reconciled_note_en: 'standing in a forest',
  final_prompt:
    'transparent background, centered, facing camera, female character, ink wash style, standing in a forest',
}

const HAPPY_ALIAS_RESPONSE: PromptPreviewResponse = {
  ...HAPPY_BASE_RESPONSE,
  menu_fragments: [],
  reconciled_note_en: 'switch outfit to red qipao, keep face identical',
  final_prompt:
    'transparent background, centered, facing camera, switch outfit to red qipao, keep face identical',
  derived_from: { base_id: 'base-1', base_image_url: 'https://img/base-1.png' },
}

const HAPPY_MOTION_PRESET_RESPONSE: PromptPreviewResponse = {
  platform_constraints: 'transparent background, centered, facing camera',
  menu_fragments: [],
  reconciled_note_en: '',
  final_prompt: 'wave hand greeting, identity preserved',
  parent: { type: 'base', id: 'base-1', image_url: 'https://img/base-1.png' },
  motion_template_used: 'preset_wave',
}

const HAPPY_MOTION_CUSTOM_RESPONSE: PromptPreviewResponse = {
  platform_constraints: 'transparent background, centered, facing camera',
  menu_fragments: [],
  reconciled_note_en: 'wave then bow politely',
  final_prompt: 'transparent background, centered, facing camera, wave then bow politely',
  parent: { type: 'alias', id: 'alias-1', image_url: 'https://img/alias-1.png' },
  motion_template_used: 'custom_reconciled',
}

function renderModal(request: PromptPreviewRequest = BASE_REQUEST, open = true) {
  const onClose = vi.fn()
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false, gcTime: 0 },
      mutations: { retry: false },
    },
  })
  const utils = render(
    <QueryClientProvider client={client}>
      <PromptPreviewModal isOpen={open} onClose={onClose} request={request} />
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

describe('PromptPreviewModal — create_base', () => {
  it('does not call the API when closed', () => {
    renderModal(BASE_REQUEST, false)
    expect(previewPromptMock).not.toHaveBeenCalled()
  })

  it('shows the loading state, then the four sections on success', async () => {
    let resolve: (value: PromptPreviewResponse) => void = () => {}
    previewPromptMock.mockImplementation(() => new Promise((r) => (resolve = r)))

    renderModal()

    expect(screen.getByTestId('prompt-preview-loading')).toBeInTheDocument()
    expect(previewPromptMock).toHaveBeenCalledTimes(1)
    expect(previewPromptMock).toHaveBeenCalledWith(BASE_REQUEST)

    resolve(HAPPY_BASE_RESPONSE)

    expect(await screen.findByText('平台固定 constraints')).toBeInTheDocument()
    expect(screen.getByText('選單片段')).toBeInTheDocument()
    expect(screen.getByText('重寫後的補述（英文）')).toBeInTheDocument()
    expect(screen.getByText('最終 prompt')).toBeInTheDocument()
    expect(screen.getByTestId('prompt-preview-final')).toHaveTextContent(
      HAPPY_BASE_RESPONSE.final_prompt,
    )
    const fragments = screen.getByTestId('prompt-preview-menu-fragments')
    expect(fragments).toHaveTextContent('female character')
    expect(fragments).toHaveTextContent('ink wash style')

    // No alias / motion context header in base mode.
    expect(screen.queryByTestId('prompt-preview-alias-context')).not.toBeInTheDocument()
    expect(screen.queryByTestId('prompt-preview-motion-context')).not.toBeInTheDocument()
  })

  it('forwards base_checkpoint_id when in remix mode (closes S2-5)', async () => {
    previewPromptMock.mockResolvedValue(HAPPY_BASE_RESPONSE)
    renderModal(REMIX_REQUEST)
    await screen.findByText('最終 prompt')
    expect(previewPromptMock).toHaveBeenCalledWith(REMIX_REQUEST)
    // The legacy "remix unsupported" inline notice no longer renders — preview
    // is now faithful via the new base_checkpoint_id wire field.
    expect(screen.queryByTestId('prompt-preview-unsupported')).not.toBeInTheDocument()
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
    previewPromptMock.mockResolvedValue(HAPPY_BASE_RESPONSE)
    renderModal()

    const copyBtn = await screen.findByTestId('prompt-preview-copy')
    fireEvent.click(copyBtn)

    await waitFor(() =>
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith(HAPPY_BASE_RESPONSE.final_prompt),
    )
    await waitFor(() => expect(sonnerCalls).toContainEqual({ kind: 'success', message: '已複製' }))
  })

  it('invokes onClose when the dialog is dismissed', async () => {
    previewPromptMock.mockResolvedValue(HAPPY_BASE_RESPONSE)
    const { onClose } = renderModal()

    await screen.findByText('最終 prompt')
    fireEvent.keyDown(document.body, { key: 'Escape' })

    await waitFor(() => expect(onClose).toHaveBeenCalled())
  })

  it('re-fetches when the modal closes and reopens', async () => {
    previewPromptMock.mockResolvedValue(HAPPY_BASE_RESPONSE)
    const onClose = vi.fn()
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false, refetchOnWindowFocus: false, gcTime: 0 },
        mutations: { retry: false },
      },
    })
    const { rerender } = render(
      <QueryClientProvider client={client}>
        <PromptPreviewModal isOpen onClose={onClose} request={BASE_REQUEST} />
      </QueryClientProvider>,
    )
    await screen.findByText('最終 prompt')
    expect(previewPromptMock).toHaveBeenCalledTimes(1)

    rerender(
      <QueryClientProvider client={client}>
        <PromptPreviewModal isOpen={false} onClose={onClose} request={BASE_REQUEST} />
      </QueryClientProvider>,
    )
    rerender(
      <QueryClientProvider client={client}>
        <PromptPreviewModal isOpen onClose={onClose} request={BASE_REQUEST} />
      </QueryClientProvider>,
    )

    // Radix unmounts DialogContent on close, so reopening mounts a fresh
    // useQuery — the ticket requires this so the user sees the latest
    // reconciler output (backend Redis caches duplicate work).
    await waitFor(() => expect(previewPromptMock).toHaveBeenCalledTimes(2))
  })

  it('toasts an error when clipboard write fails', async () => {
    previewPromptMock.mockResolvedValue(HAPPY_BASE_RESPONSE)
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

describe('PromptPreviewModal — create_alias', () => {
  it('renders the derived_from base thumbnail and alias input summary', async () => {
    previewPromptMock.mockResolvedValue(HAPPY_ALIAS_RESPONSE)
    renderModal(ALIAS_REQUEST)

    const ctx = await screen.findByTestId('prompt-preview-alias-context')
    expect(ctx).toBeInTheDocument()
    expect(screen.getByTestId('prompt-preview-alias-thumb').querySelector('img')).toHaveAttribute(
      'src',
      HAPPY_ALIAS_RESPONSE.derived_from!.base_image_url,
    )
    expect(screen.getByTestId('prompt-preview-alias-input-mode')).toHaveTextContent(
      '文字 + 參考圖',
    )
    expect(ctx).toHaveTextContent('改成紅旗袍')
    expect(ctx).toHaveTextContent('2 張')
    expect(screen.getByTestId('prompt-preview-alias-mask')).toHaveTextContent('已圈選')

    // Alias modes always go through the reconciler — final prompt + reconciled
    // note both render.
    expect(screen.getByTestId('prompt-preview-final')).toHaveTextContent(
      HAPPY_ALIAS_RESPONSE.final_prompt,
    )
    expect(screen.getByTestId('prompt-preview-reconciled-note')).toHaveTextContent(
      HAPPY_ALIAS_RESPONSE.reconciled_note_en,
    )
  })

  it('shows the friendly hint for VALIDATION_MASK_REQUIRED (empty inpaint mask)', async () => {
    previewPromptMock.mockRejectedValue(
      new ApiError(422, 'VALIDATION_MASK_REQUIRED', '需要 mask 內容', {
        error: {
          code: 'VALIDATION_MASK_REQUIRED',
          message: '需要 mask 內容',
          retryable: false,
        },
      }),
    )

    renderModal(ALIAS_REQUEST)

    expect(await screen.findByTestId('prompt-preview-error-mask')).toHaveTextContent(
      '請先在畫布上圈選要編輯的區域',
    )
  })
})

describe('PromptPreviewModal — create_motion', () => {
  it('preset motion: shows the preset badge and skips the reconciler block', async () => {
    previewPromptMock.mockResolvedValue(HAPPY_MOTION_PRESET_RESPONSE)
    renderModal(MOTION_PRESET_REQUEST)

    await screen.findByTestId('prompt-preview-motion-context')
    expect(screen.getByTestId('prompt-preview-motion-thumb').querySelector('img')).toHaveAttribute(
      'src',
      HAPPY_MOTION_PRESET_RESPONSE.parent!.image_url,
    )
    expect(screen.getByTestId('prompt-preview-motion-type')).toHaveTextContent('preset_wave')
    expect(screen.getByTestId('prompt-preview-motion-preset-badge')).toHaveTextContent(
      '使用平台預設模板',
    )

    // Reconciler block (menu_fragments + reconciled_note) is suppressed —
    // a preset motion uses a fixed platform template, no reconciler call.
    expect(screen.queryByText('選單片段')).not.toBeInTheDocument()
    expect(screen.queryByText('重寫後的補述（英文）')).not.toBeInTheDocument()
    expect(screen.queryByTestId('prompt-preview-menu-fragments')).not.toBeInTheDocument()
    expect(screen.queryByTestId('prompt-preview-reconciled-note')).not.toBeInTheDocument()

    // platform_constraints + final_prompt still surface so the user can
    // audit what Veo will actually receive.
    expect(screen.getByText('平台固定 constraints')).toBeInTheDocument()
    expect(screen.getByTestId('prompt-preview-final')).toHaveTextContent(
      HAPPY_MOTION_PRESET_RESPONSE.final_prompt,
    )
  })

  it('custom motion: keeps the reconciler block and renders the description', async () => {
    previewPromptMock.mockResolvedValue(HAPPY_MOTION_CUSTOM_RESPONSE)
    renderModal(MOTION_CUSTOM_REQUEST)

    const ctx = await screen.findByTestId('prompt-preview-motion-context')
    expect(ctx).toHaveTextContent('Alias')
    expect(ctx).toHaveTextContent('揮手後鞠躬')

    // No preset badge for custom motions.
    expect(screen.queryByTestId('prompt-preview-motion-preset-badge')).not.toBeInTheDocument()

    // Reconciler block is back.
    expect(screen.getByText('重寫後的補述（英文）')).toBeInTheDocument()
    expect(screen.getByTestId('prompt-preview-reconciled-note')).toHaveTextContent(
      HAPPY_MOTION_CUSTOM_RESPONSE.reconciled_note_en,
    )
    expect(screen.getByTestId('prompt-preview-final')).toHaveTextContent(
      HAPPY_MOTION_CUSTOM_RESPONSE.final_prompt,
    )
  })
})
