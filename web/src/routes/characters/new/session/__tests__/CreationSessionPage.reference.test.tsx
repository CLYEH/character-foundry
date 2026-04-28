import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import CreationSessionPage from '../CreationSessionPage'
import {
  createCheckpoint,
  getCreationSession,
  type Checkpoint,
  type CreateCheckpointResponse,
  type CreationSessionDetail,
} from '@/api/endpoints/checkpoints'
import { uploadReferenceImage } from '@/api/endpoints/reference-images'
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

vi.mock('@/api/endpoints/reference-images', async () => {
  const actual =
    await vi.importActual<typeof import('@/api/endpoints/reference-images')>(
      '@/api/endpoints/reference-images',
    )
  return { ...actual, uploadReferenceImage: vi.fn() }
})

vi.mock('@/api/endpoints/tasks', async () => {
  const actual =
    await vi.importActual<typeof import('@/api/endpoints/tasks')>('@/api/endpoints/tasks')
  return { ...actual, cancelTask: vi.fn() }
})

// SSE never fires in these tests — we only assert on upload + payload flow.
vi.mock('@microsoft/fetch-event-source', () => ({
  fetchEventSource: () =>
    new Promise<void>(() => {
      /* never resolves */
    }),
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
const uploadReferenceImageMock = vi.mocked(uploadReferenceImage)

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const SESSION_ID = '66666666-6666-6666-6666-666666666666'
const ME_ID = '11111111-1111-1111-1111-111111111111'

const PNG_SIGNATURE = new Uint8Array([
  0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0x00, 0x00, 0x00,
])

function makePngFile(name: string, padBytes = 0): File {
  // Pads with zero bytes so we can hit the size limit in tests without
  // allocating real megabytes of PNG body.
  const body = new Uint8Array(PNG_SIGNATURE.length + padBytes)
  body.set(PNG_SIGNATURE, 0)
  return new File([body], name, { type: 'image/png' })
}

function makeOversizedFile(name: string): File {
  // Just past the 10MB limit. We still set the PNG signature so a
  // future read past the size guard won't fail on MIME sniff first.
  const padded = 10 * 1024 * 1024 + 1
  return makePngFile(name, padded - PNG_SIGNATURE.length)
}

function makeUnsupportedFile(name: string): File {
  // Leading bytes don't match any of the allowed magic numbers; this
  // is what an arbitrary text/plain payload would look like.
  const body = new Uint8Array([0x47, 0x49, 0x46, 0x38])
  return new File([body], name, { type: 'image/gif' })
}

function makeEmptyMimePngFile(name: string): File {
  // Bytes are a valid PNG signature, but `File.type` is empty — the
  // drag-drop edge case where the OS didn't supply a MIME.
  const body = new Uint8Array(PNG_SIGNATURE.length)
  body.set(PNG_SIGNATURE, 0)
  return new File([body], name, { type: '' })
}

function makeMismatchedMimeFile(name: string): File {
  // PNG signature inside, but the declared MIME claims JPEG — classic
  // extension-rename spoof attempt.
  const body = new Uint8Array(PNG_SIGNATURE.length)
  body.set(PNG_SIGNATURE, 0)
  return new File([body], name, { type: 'image/jpeg' })
}

function makeReferenceSession(): CreationSessionDetail {
  return {
    session: {
      id: SESSION_ID,
      character_id: 'char-id',
      input_mode: 'reference',
      status: 'in_progress',
      checkpoint_count: 0,
      created_at: '2026-04-28T08:00:00Z',
      completed_at: null,
    },
    checkpoints: [],
  }
}

function makeCheckpoint(overrides: Partial<Checkpoint> = {}): Checkpoint {
  return {
    id: 'cp-existing',
    creation_session_id: SESSION_ID,
    sequence: 1,
    prompt_summary: 'reference summary',
    output_image_url: 'https://img/full.png',
    thumbnail_url: 'https://img/thumb.png',
    selected_as_base: false,
    created_at: '2026-04-28T08:05:00Z',
    ...overrides,
  }
}

function renderPage(extraRouteElement?: ReactNode) {
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
            <Route
              path="/characters/new/session/:id"
              element={
                <>
                  <CreationSessionPage />
                  {extraRouteElement}
                </>
              }
            />
          </Routes>
        </MemoryRouter>
      </TooltipProvider>
    </QueryClientProvider>,
  )
}

function NavigateButton({ to, label }: { to: string; label: string }) {
  const navigate = useNavigate()
  return (
    <button type="button" onClick={() => navigate(to)} data-testid={`nav-${label}`}>
      {label}
    </button>
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
  sonnerCalls.length = 0
  getCreationSessionMock.mockReset()
  createCheckpointMock.mockReset()
  uploadReferenceImageMock.mockReset()
  // jsdom does not implement object URLs.
  if (!URL.createObjectURL) {
    Object.defineProperty(URL, 'createObjectURL', {
      value: vi.fn(() => 'blob:preview'),
      configurable: true,
    })
  }
  if (!URL.revokeObjectURL) {
    Object.defineProperty(URL, 'revokeObjectURL', {
      value: vi.fn(),
      configurable: true,
    })
  }
})

afterEach(() => {
  act(() => {
    useAuthStore.setState({ accessToken: null, refreshToken: null, user: null, expiresAt: null })
  })
})

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('CreationSessionPage — reference mode', () => {
  it('renders the upload dropzone instead of the template menu when input_mode is reference', async () => {
    getCreationSessionMock.mockResolvedValue(makeReferenceSession())
    renderPage()
    expect(await screen.findByTestId('reference-image-dropzone')).toBeInTheDocument()
    // Template-mode select trigger should not be in the DOM.
    expect(screen.queryByLabelText('性別')).not.toBeInTheDocument()
  })

  it('disables 生成新候選 until at least one reference image has uploaded', async () => {
    getCreationSessionMock.mockResolvedValue(makeReferenceSession())
    renderPage()
    const generate = await screen.findByRole('button', { name: '生成新候選' })
    expect(generate).toBeDisabled()
  })

  it('uploads three PNGs, stores their ids, and sends them in the checkpoint payload', async () => {
    getCreationSessionMock.mockResolvedValue(makeReferenceSession())
    uploadReferenceImageMock
      .mockResolvedValueOnce({ reference_image_id: 'ref-1', url: 'https://signed/1' })
      .mockResolvedValueOnce({ reference_image_id: 'ref-2', url: 'https://signed/2' })
      .mockResolvedValueOnce({ reference_image_id: 'ref-3', url: 'https://signed/3' })
    createCheckpointMock.mockResolvedValue({
      task_id: 'task-r',
      checkpoint_id: 'cp-r',
    } satisfies CreateCheckpointResponse)
    renderPage()

    await screen.findByTestId('reference-image-dropzone')
    const input = screen.getByTestId('reference-image-input') as HTMLInputElement

    const files = [makePngFile('a.png'), makePngFile('b.png'), makePngFile('c.png')]
    await act(async () => {
      fireEvent.change(input, { target: { files } })
    })

    await waitFor(() => {
      expect(screen.getAllByTestId(/^reference-image-preview-/)).toHaveLength(3)
    })
    await waitFor(() =>
      expect(uploadReferenceImageMock).toHaveBeenCalledTimes(3),
    )

    const generate = await screen.findByRole('button', { name: '生成新候選' })
    await waitFor(() => expect(generate).toBeEnabled())
    fireEvent.click(generate)

    await waitFor(() => expect(createCheckpointMock).toHaveBeenCalledTimes(1))
    const body = createCheckpointMock.mock.calls[0]?.[1]
    expect(body?.reference_image_ids).toEqual(['ref-1', 'ref-2', 'ref-3'])
    expect(body?.menu_selections).toBeNull()
    expect(body?.mode).toBe('fresh')
  })

  it('rejects a >10MB file with a toast and never calls the upload endpoint', async () => {
    getCreationSessionMock.mockResolvedValue(makeReferenceSession())
    renderPage()

    await screen.findByTestId('reference-image-dropzone')
    const input = screen.getByTestId('reference-image-input') as HTMLInputElement

    await act(async () => {
      fireEvent.change(input, { target: { files: [makeOversizedFile('huge.png')] } })
    })

    expect(uploadReferenceImageMock).not.toHaveBeenCalled()
    await waitFor(() =>
      expect(sonnerCalls.find((c) => c.kind === 'error')?.message).toMatch(/上限 10 MB/),
    )
  })

  it('rejects a 4th file when 3 are already pending', async () => {
    getCreationSessionMock.mockResolvedValue(makeReferenceSession())
    uploadReferenceImageMock.mockImplementation((_sessionId, file) =>
      Promise.resolve({
        reference_image_id: `ref-${file.name}`,
        url: `https://signed/${file.name}`,
      }),
    )
    renderPage()

    await screen.findByTestId('reference-image-dropzone')
    const input = screen.getByTestId('reference-image-input') as HTMLInputElement

    await act(async () => {
      fireEvent.change(input, {
        target: { files: [makePngFile('a.png'), makePngFile('b.png'), makePngFile('c.png')] },
      })
    })

    await waitFor(() => {
      expect(screen.getAllByTestId(/^reference-image-preview-/)).toHaveLength(3)
    })

    sonnerCalls.length = 0

    await act(async () => {
      fireEvent.change(input, { target: { files: [makePngFile('d.png')] } })
    })

    expect(screen.getAllByTestId(/^reference-image-preview-/)).toHaveLength(3)
    await waitFor(() =>
      expect(sonnerCalls.find((c) => c.kind === 'error')?.message).toMatch(/最多 3 張/),
    )
  })

  it('rejects an unsupported MIME type via magic-byte sniff', async () => {
    getCreationSessionMock.mockResolvedValue(makeReferenceSession())
    renderPage()

    await screen.findByTestId('reference-image-dropzone')
    const input = screen.getByTestId('reference-image-input') as HTMLInputElement

    await act(async () => {
      fireEvent.change(input, { target: { files: [makeUnsupportedFile('gif.png')] } })
    })

    expect(uploadReferenceImageMock).not.toHaveBeenCalled()
    await waitFor(() =>
      expect(sonnerCalls.find((c) => c.kind === 'error')?.message).toMatch(/格式不支援/),
    )
  })

  it('removes a preview and drops the id from the outgoing payload', async () => {
    getCreationSessionMock.mockResolvedValue(makeReferenceSession())
    uploadReferenceImageMock
      .mockResolvedValueOnce({ reference_image_id: 'ref-1', url: 'https://signed/1' })
      .mockResolvedValueOnce({ reference_image_id: 'ref-2', url: 'https://signed/2' })
    createCheckpointMock.mockResolvedValue({ task_id: 'task-r', checkpoint_id: 'cp-r' })
    renderPage()

    await screen.findByTestId('reference-image-dropzone')
    const input = screen.getByTestId('reference-image-input') as HTMLInputElement
    await act(async () => {
      fireEvent.change(input, {
        target: { files: [makePngFile('a.png'), makePngFile('b.png')] },
      })
    })

    await waitFor(() => expect(uploadReferenceImageMock).toHaveBeenCalledTimes(2))
    await waitFor(() => {
      expect(screen.getAllByTestId(/^reference-image-preview-/)).toHaveLength(2)
    })

    // Wait for both previews to be ready so we know which id maps to which.
    await waitFor(() => {
      const previews = screen.getAllByTestId(/^reference-image-preview-/)
      previews.forEach((p) => expect(p).toHaveAttribute('data-status', 'ready'))
    })

    // Remove the first preview.
    const firstPreview = screen.getAllByTestId(/^reference-image-preview-/)[0]
    const removeBtn = within(firstPreview).getByRole('button', { name: /移除/ })
    fireEvent.click(removeBtn)

    await waitFor(() => {
      expect(screen.getAllByTestId(/^reference-image-preview-/)).toHaveLength(1)
    })

    const generate = screen.getByRole('button', { name: '生成新候選' })
    await waitFor(() => expect(generate).toBeEnabled())
    fireEvent.click(generate)

    await waitFor(() => expect(createCheckpointMock).toHaveBeenCalledTimes(1))
    const body = createCheckpointMock.mock.calls[0]?.[1]
    // Whichever id we kept, only one of the two stays.
    expect(body?.reference_image_ids).toHaveLength(1)
    expect(body?.reference_image_ids?.[0]).toMatch(/^ref-[12]$/)
  })

  it('keeps 生成新候選 disabled while an upload is still in flight', async () => {
    getCreationSessionMock.mockResolvedValue(makeReferenceSession())
    let resolveUpload: (value: { reference_image_id: string; url: string }) => void = () => {}
    uploadReferenceImageMock.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveUpload = resolve
        }),
    )
    renderPage()

    await screen.findByTestId('reference-image-dropzone')
    const input = screen.getByTestId('reference-image-input') as HTMLInputElement

    await act(async () => {
      fireEvent.change(input, { target: { files: [makePngFile('a.png')] } })
    })

    await waitFor(() => {
      expect(screen.getAllByTestId(/^reference-image-preview-/)).toHaveLength(1)
    })
    const generate = screen.getByRole('button', { name: '生成新候選' })
    expect(generate).toBeDisabled()

    // Settle the upload — button flips to enabled.
    await act(async () => {
      resolveUpload({ reference_image_id: 'ref-1', url: 'https://signed/1' })
      // micro-task flush
      await Promise.resolve()
    })
    await waitFor(() => expect(generate).toBeEnabled())
  })

  it('rejects a file with empty File.type even when bytes are a valid PNG', async () => {
    // Backend gates on multipart `Content-Type` (sourced from `File.type`).
    // A drag-drop file with empty MIME would round-trip and fail
    // server-side; reject it client-side to short-circuit the wasted
    // call (Codex P2 round 4 on PR #31).
    getCreationSessionMock.mockResolvedValue(makeReferenceSession())
    renderPage()

    await screen.findByTestId('reference-image-dropzone')
    const input = screen.getByTestId('reference-image-input') as HTMLInputElement

    await act(async () => {
      fireEvent.change(input, { target: { files: [makeEmptyMimePngFile('a.png')] } })
    })

    expect(uploadReferenceImageMock).not.toHaveBeenCalled()
    await waitFor(() =>
      expect(sonnerCalls.find((c) => c.kind === 'error')?.message).toMatch(/格式不支援/),
    )
  })

  it('rejects a file whose declared MIME disagrees with the sniffed type (spoof attempt)', async () => {
    // PNG bytes inside a file declared as JPEG — classic rename spoof.
    // Belt+suspenders should catch the mismatch.
    getCreationSessionMock.mockResolvedValue(makeReferenceSession())
    renderPage()

    await screen.findByTestId('reference-image-dropzone')
    const input = screen.getByTestId('reference-image-input') as HTMLInputElement

    await act(async () => {
      fireEvent.change(input, { target: { files: [makeMismatchedMimeFile('spoofed.jpg')] } })
    })

    expect(uploadReferenceImageMock).not.toHaveBeenCalled()
    await waitFor(() =>
      expect(sonnerCalls.find((c) => c.kind === 'error')?.message).toMatch(/格式不一致|格式不支援/),
    )
  })

  it('post-await capacity recheck blocks a 4th file from two concurrent addFiles batches', async () => {
    // Two concurrent addFiles invocations both pass the pre-await
    // capacity check before either mutates `filesRef`. Without the
    // post-await recheck (Codex P2 round 3), both would fall through
    // and the cap (3) would be exceeded.
    getCreationSessionMock.mockResolvedValue(makeReferenceSession())
    uploadReferenceImageMock.mockImplementation((_sessionId, file) =>
      Promise.resolve({
        reference_image_id: `ref-${file.name}`,
        url: `https://signed/${file.name}`,
      }),
    )
    renderPage()

    await screen.findByTestId('reference-image-dropzone')
    const input = screen.getByTestId('reference-image-input') as HTMLInputElement

    // Pre-fill 2 of the 3 slots.
    await act(async () => {
      fireEvent.change(input, {
        target: { files: [makePngFile('a.png'), makePngFile('b.png')] },
      })
    })
    await waitFor(() => {
      expect(screen.getAllByTestId(/^reference-image-preview-/)).toHaveLength(2)
    })

    sonnerCalls.length = 0

    // Fire two concurrent batches in the same tick. Both pre-await
    // capacity checks see `filesRef.current.size === 2`, both pass.
    // Only the post-await recheck on the second-resuming batch keeps
    // the cap at 3.
    await act(async () => {
      fireEvent.change(input, { target: { files: [makePngFile('c.png')] } })
      fireEvent.change(input, { target: { files: [makePngFile('d.png')] } })
    })

    await waitFor(() => {
      expect(screen.getAllByTestId(/^reference-image-preview-/)).toHaveLength(3)
    })
    // Exactly one of c/d should have been rejected with the cap toast.
    expect(sonnerCalls.find((c) => c.kind === 'error')?.message).toMatch(/最多 3 張/)
  })

  it('clears reference upload state when sessionId changes (cross-session leak guard)', async () => {
    const OTHER_SESSION_ID = '77777777-7777-7777-7777-777777777777'
    // The same CreationSessionPage component stays mounted across `:id`
    // changes (same route pattern). Without the per-sessionId reset,
    // session A's reference_image_ids would leak into session B's
    // submit payload — the backend would reject those ids with
    // NOT_FOUND_REFERENCE_IMAGE.
    getCreationSessionMock.mockImplementation((id: string) =>
      Promise.resolve({
        ...makeReferenceSession(),
        session: { ...makeReferenceSession().session, id },
      }),
    )
    uploadReferenceImageMock.mockResolvedValue({
      reference_image_id: 'ref-leak',
      url: 'https://signed/leak',
    })
    createCheckpointMock.mockResolvedValue({ task_id: 'task-x', checkpoint_id: 'cp-x' })
    renderPage(
      <NavigateButton
        to={`/characters/new/session/${OTHER_SESSION_ID}`}
        label="other-session"
      />,
    )

    await screen.findByTestId('reference-image-dropzone')
    const input = screen.getByTestId('reference-image-input') as HTMLInputElement
    await act(async () => {
      fireEvent.change(input, { target: { files: [makePngFile('a.png')] } })
    })
    await waitFor(() => {
      expect(screen.getAllByTestId(/^reference-image-preview-/)).toHaveLength(1)
    })
    // Wait for the upload to settle so the id is in `referenceImageIds`.
    await waitFor(() => {
      const preview = screen.getByTestId(/^reference-image-preview-/)
      expect(preview).toHaveAttribute('data-status', 'ready')
    })

    // Navigate to a different session — same route pattern, same
    // mounted component instance.
    fireEvent.click(screen.getByTestId('nav-other-session'))

    // The session-detail query refetches on the new key, so the page
    // briefly shows Skeleton; wait for the new dropzone to mount.
    await screen.findByTestId('reference-image-dropzone')
    expect(screen.queryByTestId(/^reference-image-preview-/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '生成新候選' })).toBeDisabled()
  })

  it('completed-checkpoint sanity: surfaces existing checkpoints in reference mode too', async () => {
    getCreationSessionMock.mockResolvedValue({
      session: makeReferenceSession().session,
      checkpoints: [makeCheckpoint({ id: 'cp-1', sequence: 1 })],
    })
    renderPage()
    expect(await screen.findByTestId('checkpoint-card-cp-1')).toBeInTheDocument()
  })
})
