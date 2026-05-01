import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import CharacterDetailPage from '../CharacterDetailPage'
import { ApiError } from '@/api/client'
import {
  deleteAlias,
  listAliases,
  patchAlias,
  type Alias,
  type AliasListResponse,
  type AliasResponse,
} from '@/api/endpoints/aliases'
import {
  getCharacter,
  type CharacterDetail,
  type CharacterDetailResponse,
} from '@/api/endpoints/characters'
import { getMe, type MeResponse } from '@/api/endpoints/auth'
import {
  listAliasMotions,
  listBaseMotions,
  type Motion,
  type MotionListResponse,
} from '@/api/endpoints/motions'
import { TooltipProvider } from '@/components/ui/tooltip'
import { useAuthStore } from '@/stores/authStore'

vi.mock('@/api/endpoints/characters', async () => {
  const actual = await vi.importActual<typeof import('@/api/endpoints/characters')>(
    '@/api/endpoints/characters',
  )
  return { ...actual, getCharacter: vi.fn() }
})
vi.mock('@/api/endpoints/aliases', async () => {
  const actual =
    await vi.importActual<typeof import('@/api/endpoints/aliases')>('@/api/endpoints/aliases')
  return {
    ...actual,
    listAliases: vi.fn(),
    patchAlias: vi.fn(),
    deleteAlias: vi.fn(),
  }
})
vi.mock('@/api/endpoints/motions', async () => {
  const actual =
    await vi.importActual<typeof import('@/api/endpoints/motions')>('@/api/endpoints/motions')
  return {
    ...actual,
    listAliasMotions: vi.fn(),
    listBaseMotions: vi.fn(),
  }
})
vi.mock('@/api/endpoints/auth', async () => {
  const actual =
    await vi.importActual<typeof import('@/api/endpoints/auth')>('@/api/endpoints/auth')
  return { ...actual, getMe: vi.fn() }
})

const getCharacterMock = vi.mocked(getCharacter)
const listAliasesMock = vi.mocked(listAliases)
const patchAliasMock = vi.mocked(patchAlias)
const deleteAliasMock = vi.mocked(deleteAlias)
const listAliasMotionsMock = vi.mocked(listAliasMotions)
const listBaseMotionsMock = vi.mocked(listBaseMotions)
const getMeMock = vi.mocked(getMe)

const ME_ID = '11111111-1111-1111-1111-111111111111'
const OTHER_USER_ID = '99999999-9999-9999-9999-999999999999'
const CHARACTER_ID = 'aaaaaaaa-0000-0000-0000-000000000111'
const BASE_ID = 'bbbbbbbb-0000-0000-0000-000000000222'
const ALIAS_RED_ID = 'cccccccc-0000-0000-0000-000000000333'
const ALIAS_BLUE_ID = 'dddddddd-0000-0000-0000-000000000444'

function makeDetail(overrides: Partial<CharacterDetail> = {}): CharacterDetail {
  return {
    id: CHARACTER_ID,
    name: '小雅',
    slug: 'xiao-ya',
    owner: { id: ME_ID, name: 'Leo' },
    base: {
      id: BASE_ID,
      character_id: CHARACTER_ID,
      image_url: 'https://img/base.png',
      thumbnail_url: 'https://img/base-thumb.png',
      from_checkpoint_id: 'cp-source',
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

function makeResponse(overrides: Partial<CharacterDetail> = {}): CharacterDetailResponse {
  return { character: makeDetail(overrides) }
}

function makeAlias(overrides: Partial<Alias> = {}): Alias {
  return {
    id: ALIAS_RED_ID,
    character_id: CHARACTER_ID,
    name: '紅旗袍版',
    input_mode: 'image2image',
    image_url: 'https://img/alias-red.png',
    thumbnail_url: 'https://img/alias-red-thumb.png',
    motion_count: 0,
    created_at: '2026-04-28T11:00:00Z',
    ...overrides,
  }
}

function makeMotion(overrides: Partial<Motion> = {}): Motion {
  return {
    id: 'mmmm0001-0000-0000-0000-000000000001',
    parent: { type: 'alias', id: ALIAS_RED_ID },
    motion_type: 'preset_wave',
    name: '招手',
    description: null,
    video_url: 'https://video/wave.mp4',
    thumbnail_url: 'https://video/wave-thumb.png',
    duration_ms: 3500,
    created_at: '2026-04-28T11:30:00Z',
    ...overrides,
  }
}

function meResponse(userId: string = ME_ID): MeResponse {
  return {
    user: {
      id: userId,
      name: userId === ME_ID ? 'Leo' : 'Other',
      email: 'leo@example.com',
      team_id: '22222222-2222-2222-2222-222222222222',
      created_at: '2026-04-28T00:00:00Z',
    },
  }
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
        <MemoryRouter initialEntries={[`/characters/${CHARACTER_ID}`]}>
          <Routes>
            <Route path="/" element={<div data-testid="dashboard-stub">dashboard</div>} />
            <Route
              path="/characters/new/session/:id"
              element={<div data-testid="session-stub">session</div>}
            />
            <Route
              path="/characters/:id/aliases/new"
              element={<div data-testid="alias-edit-stub">alias edit</div>}
            />
            <Route path="/characters/:id" element={<CharacterDetailPage />} />
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

describe('CharacterDetailPage', () => {
  beforeEach(() => {
    seedAuth()
    getCharacterMock.mockReset()
    listAliasesMock.mockReset()
    patchAliasMock.mockReset()
    deleteAliasMock.mockReset()
    listAliasMotionsMock.mockReset()
    listBaseMotionsMock.mockReset()
    getMeMock.mockReset()

    // Sensible defaults; individual tests override as needed.
    listAliasesMock.mockResolvedValue({ items: [] } satisfies AliasListResponse)
    listAliasMotionsMock.mockResolvedValue({ items: [] } satisfies MotionListResponse)
    listBaseMotionsMock.mockResolvedValue({ items: [] } satisfies MotionListResponse)
    getMeMock.mockResolvedValue(meResponse(ME_ID))
  })

  afterEach(() => {
    act(() => {
      useAuthStore.setState({ accessToken: null, refreshToken: null, user: null, expiresAt: null })
    })
  })

  it('renders the skeleton while the detail query is pending', () => {
    getCharacterMock.mockImplementation(() => new Promise(() => {}))
    renderPage()
    expect(screen.getByTestId('character-detail-skeleton')).toBeInTheDocument()
  })

  it('renders the header, base card, motion strip, and empty alias state', async () => {
    getCharacterMock.mockResolvedValue(makeResponse())
    renderPage()
    await screen.findByTestId('character-detail-name')
    expect(screen.getByTestId('character-detail-name')).toHaveTextContent('小雅')
    expect(screen.getByTestId('character-detail-owner')).toHaveTextContent('by Leo')
    expect(screen.getByTestId('base-card')).toBeInTheDocument()
    expect(screen.getByTestId('base-card-image')).toHaveAttribute('src', 'https://img/base.png')
    // Motion strip on the Base shows the 5 preset slots disabled with the
    // Sprint-3 tooltip — generation lands in T-038.
    expect(screen.getByTestId(`motion-row-base-${BASE_ID}`)).toBeInTheDocument()
    expect(screen.getByTestId('motion-cell-empty-preset_wave')).toBeDisabled()
    // Aliases section is empty → AliasEmptyState with enabled CTA link.
    await waitFor(() => {
      expect(screen.getByTestId('alias-empty-state')).toBeInTheDocument()
    })
    expect(screen.getByTestId('alias-empty-create-cta')).toHaveAttribute(
      'href',
      `/characters/${CHARACTER_ID}/aliases/new`,
    )
  })

  it('opens the read-only prompt modal when 查看完整 prompt is clicked', async () => {
    getCharacterMock.mockResolvedValue(makeResponse())
    renderPage()
    await screen.findByTestId('base-card')
    fireEvent.click(screen.getByTestId('base-view-prompt'))
    expect(await screen.findByTestId('base-prompt-modal')).toBeInTheDocument()
  })

  it('shows the resume CTA when base is null and session is in_progress', async () => {
    const sessionId = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
    getCharacterMock.mockResolvedValue(
      makeResponse({
        base: null,
        creation_session: { id: sessionId, status: 'in_progress' },
      }),
    )
    renderPage()
    expect(await screen.findByTestId('character-detail-resume-in-progress')).toBeInTheDocument()
    expect(screen.queryByTestId('dashboard-stub')).not.toBeInTheDocument()
    expect(screen.queryByTestId('session-stub')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: /繼續建立/ })).toHaveAttribute(
      'href',
      `/characters/new/session/${sessionId}`,
    )
    expect(screen.getByRole('link', { name: /回 Dashboard/ })).toHaveAttribute('href', '/')
  })

  it('shows the abandoned-session message (no resume CTA) when session is abandoned', async () => {
    getCharacterMock.mockResolvedValue(
      makeResponse({
        base: null,
        creation_session: {
          id: 'ffffffff-1111-2222-3333-444444444444',
          status: 'abandoned',
        },
      }),
    )
    renderPage()
    expect(await screen.findByTestId('character-detail-session-abandoned')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /繼續建立/ })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: /回 Dashboard/ })).toHaveAttribute('href', '/')
  })

  it('falls back to the inline error when base and session are both null', async () => {
    getCharacterMock.mockResolvedValue(makeResponse({ base: null, creation_session: null }))
    renderPage()
    expect(await screen.findByTestId('character-detail-no-base')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /繼續建立/ })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: /回 Dashboard/ })).toHaveAttribute('href', '/')
  })

  it('renders multiple aliases with their motion thumbnails and counts', async () => {
    getCharacterMock.mockResolvedValue(makeResponse())
    listAliasesMock.mockResolvedValue({
      items: [
        makeAlias({ id: ALIAS_RED_ID, name: '紅旗袍版', motion_count: 1 }),
        makeAlias({
          id: ALIAS_BLUE_ID,
          name: '藍旗袍版',
          image_url: 'https://img/alias-blue.png',
          motion_count: 0,
        }),
      ],
    } satisfies AliasListResponse)
    listAliasMotionsMock.mockImplementation((aliasId: string) =>
      Promise.resolve({
        items:
          aliasId === ALIAS_RED_ID
            ? [
                makeMotion({
                  id: 'motion-red-wave',
                  parent: { type: 'alias', id: ALIAS_RED_ID },
                  motion_type: 'preset_wave',
                  name: '招手',
                }),
              ]
            : [],
      } satisfies MotionListResponse),
    )

    renderPage()
    await screen.findByTestId(`alias-row-${ALIAS_RED_ID}`)
    expect(screen.getByTestId(`alias-row-name-${ALIAS_RED_ID}`)).toHaveTextContent('紅旗袍版')
    expect(screen.getByTestId(`alias-row-name-${ALIAS_BLUE_ID}`)).toHaveTextContent('藍旗袍版')
    // The red alias has its preset_wave generated → completed cell
    // (with thumbnail). The four other preset slots stay empty.
    await waitFor(() => {
      expect(screen.getByTestId('motion-cell-completed-motion-red-wave')).toBeInTheDocument()
    })
    const redRow = screen.getByTestId(`motion-row-alias-${ALIAS_RED_ID}`)
    expect(within(redRow).getByText(/1\/5 預設 \+ 0 自訂/)).toBeInTheDocument()
  })

  it('navigates to the alias edit page from the empty-state CTA', async () => {
    getCharacterMock.mockResolvedValue(makeResponse())
    renderPage()
    await screen.findByTestId('alias-empty-state')
    fireEvent.click(screen.getByTestId('alias-empty-create-cta'))
    await screen.findByTestId('alias-edit-stub')
  })

  it('navigates to the alias edit page from the section CTA when aliases exist', async () => {
    getCharacterMock.mockResolvedValue(makeResponse())
    listAliasesMock.mockResolvedValue({ items: [makeAlias()] } satisfies AliasListResponse)
    renderPage()
    await screen.findByTestId('alias-create-cta')
    fireEvent.click(screen.getByTestId('alias-create-cta'))
    await screen.findByTestId('alias-edit-stub')
  })

  it('renames an alias inline and refreshes the list', async () => {
    getCharacterMock.mockResolvedValue(makeResponse())
    listAliasesMock.mockResolvedValueOnce({ items: [makeAlias()] } satisfies AliasListResponse)
    listAliasesMock.mockResolvedValue({
      items: [makeAlias({ name: '紅旗袍版-v2' })],
    } satisfies AliasListResponse)
    patchAliasMock.mockResolvedValue({
      alias: makeAlias({ name: '紅旗袍版-v2' }),
    } satisfies AliasResponse)

    renderPage()
    await screen.findByTestId(`alias-row-${ALIAS_RED_ID}`)

    fireEvent.click(screen.getByTestId(`alias-row-rename-${ALIAS_RED_ID}`))
    const input = await screen.findByTestId(`alias-rename-input-${ALIAS_RED_ID}`)
    fireEvent.change(input, { target: { value: '紅旗袍版-v2' } })
    fireEvent.click(screen.getByTestId(`alias-rename-submit-${ALIAS_RED_ID}`))

    await waitFor(() => {
      expect(patchAliasMock).toHaveBeenCalledWith(ALIAS_RED_ID, { name: '紅旗袍版-v2' })
    })
    await waitFor(() => {
      expect(screen.getByTestId(`alias-row-name-${ALIAS_RED_ID}`)).toHaveTextContent('紅旗袍版-v2')
    })
    // Form closes after success.
    expect(screen.queryByTestId(`alias-rename-form-${ALIAS_RED_ID}`)).not.toBeInTheDocument()
  })

  it('surfaces the backend message on a duplicate-name rename failure', async () => {
    getCharacterMock.mockResolvedValue(makeResponse())
    listAliasesMock.mockResolvedValue({ items: [makeAlias()] } satisfies AliasListResponse)
    patchAliasMock.mockRejectedValue(
      new ApiError(409, 'CONFLICT_DUPLICATE_NAME', '名稱已存在', {
        error: { code: 'CONFLICT_DUPLICATE_NAME', message: '名稱已存在' },
      }),
    )

    renderPage()
    await screen.findByTestId(`alias-row-${ALIAS_RED_ID}`)
    fireEvent.click(screen.getByTestId(`alias-row-rename-${ALIAS_RED_ID}`))
    const input = await screen.findByTestId(`alias-rename-input-${ALIAS_RED_ID}`)
    fireEvent.change(input, { target: { value: '別的名字' } })
    fireEvent.click(screen.getByTestId(`alias-rename-submit-${ALIAS_RED_ID}`))

    expect(await screen.findByTestId(`alias-rename-error-${ALIAS_RED_ID}`)).toHaveTextContent(
      '名稱已存在',
    )
    // Form stays open so the user can correct + resubmit.
    expect(screen.getByTestId(`alias-rename-form-${ALIAS_RED_ID}`)).toBeInTheDocument()
  })

  it('deletes an alias via the confirm dialog and removes it from the list', async () => {
    getCharacterMock.mockResolvedValue(makeResponse())
    listAliasesMock.mockResolvedValueOnce({ items: [makeAlias()] } satisfies AliasListResponse)
    listAliasesMock.mockResolvedValue({ items: [] } satisfies AliasListResponse)
    deleteAliasMock.mockResolvedValue(undefined)

    renderPage()
    await screen.findByTestId(`alias-row-${ALIAS_RED_ID}`)
    fireEvent.click(screen.getByTestId(`alias-row-delete-${ALIAS_RED_ID}`))
    expect(await screen.findByTestId('alias-delete-confirm')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('alias-delete-confirm-action'))

    await waitFor(() => {
      expect(deleteAliasMock).toHaveBeenCalledWith(ALIAS_RED_ID)
    })
    await waitFor(() => {
      expect(screen.queryByTestId(`alias-row-${ALIAS_RED_ID}`)).not.toBeInTheDocument()
    })
    expect(screen.getByTestId('alias-empty-state')).toBeInTheDocument()
  })

  it('renders disabled action buttons + tooltip when the viewer is not the owner', async () => {
    getCharacterMock.mockResolvedValue(
      makeResponse({ owner: { id: OTHER_USER_ID, name: 'Other' } }),
    )
    listAliasesMock.mockResolvedValue({ items: [makeAlias()] } satisfies AliasListResponse)
    getMeMock.mockResolvedValue(meResponse(ME_ID))

    renderPage()
    await screen.findByTestId(`alias-row-${ALIAS_RED_ID}`)
    expect(screen.getByTestId(`alias-row-rename-${ALIAS_RED_ID}`)).toBeDisabled()
    expect(screen.getByTestId(`alias-row-delete-${ALIAS_RED_ID}`)).toBeDisabled()
    expect(screen.queryByTestId('alias-create-cta')).not.toBeInTheDocument()
  })

  it('surfaces a motion-row error band when the motions fetch fails', async () => {
    getCharacterMock.mockResolvedValue(makeResponse())
    listAliasesMock.mockResolvedValue({ items: [] } satisfies AliasListResponse)
    listBaseMotionsMock.mockRejectedValue(
      new ApiError(500, 'INTERNAL_UNEXPECTED_ERROR', '伺服器忙碌', {
        error: { code: 'INTERNAL_UNEXPECTED_ERROR', message: '伺服器忙碌' },
      }),
    )
    renderPage()
    expect(await screen.findByTestId(`motion-row-error-base-${BASE_ID}`)).toHaveTextContent(
      '伺服器忙碌',
    )
  })

  it('plays a completed motion in the lightbox', async () => {
    getCharacterMock.mockResolvedValue(makeResponse())
    listAliasesMock.mockResolvedValue({ items: [makeAlias()] } satisfies AliasListResponse)
    listAliasMotionsMock.mockResolvedValue({
      items: [
        makeMotion({
          id: 'motion-red-wave',
          parent: { type: 'alias', id: ALIAS_RED_ID },
        }),
      ],
    } satisfies MotionListResponse)

    renderPage()
    await screen.findByTestId('motion-cell-completed-motion-red-wave')
    fireEvent.click(screen.getByTestId('motion-cell-completed-motion-red-wave'))
    const lightbox = await screen.findByTestId('motion-lightbox')
    expect(within(lightbox).getByTestId('motion-lightbox-video')).toHaveAttribute(
      'src',
      'https://video/wave.mp4',
    )
  })

  it('renders NotFoundPage on 404', async () => {
    getCharacterMock.mockRejectedValue(
      new ApiError(404, 'NOT_FOUND_CHARACTER', '找不到角色', {
        error: { code: 'NOT_FOUND_CHARACTER', message: '找不到角色' },
      }),
    )
    renderPage()
    expect(await screen.findByTestId('not-found-page')).toBeInTheDocument()
  })

  it('renders a generic error page with retry on 500', async () => {
    getCharacterMock.mockRejectedValueOnce(
      new ApiError(500, 'INTERNAL_UNEXPECTED_ERROR', '伺服器錯誤', {
        error: { code: 'INTERNAL_UNEXPECTED_ERROR', message: '伺服器錯誤' },
      }),
    )
    renderPage()
    expect(await screen.findByTestId('generic-error-page')).toBeInTheDocument()

    getCharacterMock.mockResolvedValueOnce(makeResponse())
    fireEvent.click(screen.getByRole('button', { name: '重試' }))

    await waitFor(() => {
      expect(screen.getByTestId('character-detail-name')).toBeInTheDocument()
    })
  })
})
