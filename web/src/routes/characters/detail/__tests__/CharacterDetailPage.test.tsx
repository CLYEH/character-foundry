import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import CharacterDetailPage from '../CharacterDetailPage'
import { ApiError } from '@/api/client'
import {
  getCharacter,
  type CharacterDetail,
  type CharacterDetailResponse,
} from '@/api/endpoints/characters'
import { TooltipProvider } from '@/components/ui/tooltip'
import { useAuthStore } from '@/stores/authStore'

vi.mock('@/api/endpoints/characters', async () => {
  const actual = await vi.importActual<typeof import('@/api/endpoints/characters')>(
    '@/api/endpoints/characters',
  )
  return { ...actual, getCharacter: vi.fn() }
})

const getCharacterMock = vi.mocked(getCharacter)

const ME_ID = '11111111-1111-1111-1111-111111111111'
const CHARACTER_ID = 'aaaaaaaa-0000-0000-0000-000000000111'

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

  it('renders the character header, base card, and empty alias / motion sections', async () => {
    getCharacterMock.mockResolvedValue(makeResponse())
    renderPage()
    await screen.findByTestId('character-detail-name')
    expect(screen.getByTestId('character-detail-name')).toHaveTextContent('小雅')
    expect(screen.getByTestId('character-detail-owner')).toHaveTextContent('by Leo')
    expect(screen.getByTestId('base-card')).toBeInTheDocument()
    expect(screen.getByTestId('base-card-image')).toHaveAttribute('src', 'https://img/base.png')
    expect(screen.getByTestId('alias-empty-state')).toBeInTheDocument()
    expect(screen.getByTestId('motion-empty-strip')).toBeInTheDocument()
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
    // Resume CTA points at the creation session route. We did NOT
    // navigate there — the page stays mounted so the user explicitly
    // chooses to resume.
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
    // Abandoned sessions cannot be resumed (per user-flows §4.1) — the
    // 繼續建立 CTA must not appear.
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
