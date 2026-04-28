import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useParams } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import DashboardPage from '../DashboardPage'
import { listCharacters, type Character } from '@/api/endpoints/characters'
import { TooltipProvider } from '@/components/ui/tooltip'
import { useAuthStore } from '@/stores/authStore'

vi.mock('@/api/endpoints/characters', async () => {
  const actual = await vi.importActual<typeof import('@/api/endpoints/characters')>(
    '@/api/endpoints/characters',
  )
  return { ...actual, listCharacters: vi.fn() }
})

const listCharactersMock = vi.mocked(listCharacters)

const ME_ID = '11111111-1111-1111-1111-111111111111'
const TEAMMATE_ID = '99999999-9999-9999-9999-999999999999'

function makeCharacter(overrides: Partial<Character> = {}): Character {
  return {
    id: '00000000-0000-0000-0000-000000000001',
    name: '小雅',
    slug: 'xiao-ya',
    owner: { id: ME_ID, name: 'Leo' },
    base_thumbnail_url: null,
    alias_count: 2,
    motion_count: 5,
    created_at: '2026-04-20T08:15:00Z',
    updated_at: '2026-04-23T10:30:00Z',
    ...overrides,
  }
}

function CharacterDetailStub() {
  const { id } = useParams()
  return <div data-testid="character-detail-stub">character {id}</div>
}

function NewCharacterStub() {
  return <div data-testid="new-character-page">new character</div>
}

function renderDashboard() {
  // Tests need retry: false so a mocked rejection lands in `isError`
  // immediately instead of triggering the production retry-twice policy.
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false, gcTime: 0 },
    },
  })
  return render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <MemoryRouter initialEntries={['/']}>
          <Routes>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/characters/new" element={<NewCharacterStub />} />
            <Route path="/characters/:id" element={<CharacterDetailStub />} />
          </Routes>
        </MemoryRouter>
      </TooltipProvider>
    </QueryClientProvider>,
  )
}

function seedMe() {
  act(() => {
    useAuthStore.setState({
      accessToken: 'a',
      refreshToken: 'r',
      user: {
        id: ME_ID,
        name: 'Leo',
        email: 'leo@example.com',
        team_id: '22222222-2222-2222-2222-222222222222',
        created_at: '2026-04-24T00:00:00Z',
      },
      expiresAt: Date.now() + 60_000,
    })
  })
}

describe('DashboardPage', () => {
  beforeEach(() => {
    seedMe()
    listCharactersMock.mockReset()
  })

  afterEach(() => {
    act(() => {
      useAuthStore.setState({
        accessToken: null,
        refreshToken: null,
        user: null,
        expiresAt: null,
      })
    })
  })

  it('shows the loading skeleton while the list query is pending', () => {
    listCharactersMock.mockImplementation(() => new Promise(() => {}))
    renderDashboard()
    expect(screen.getByTestId('dashboard-skeleton')).toBeInTheDocument()
  })

  it('shows the empty state when the API returns no characters', async () => {
    listCharactersMock.mockResolvedValue({ items: [], next_cursor: null })
    renderDashboard()
    expect(await screen.findByTestId('dashboard-empty')).toBeInTheDocument()
    expect(screen.getByText('還沒有角色，建一個吧')).toBeInTheDocument()
  })

  it('navigates to /characters/new when the empty-state CTA is clicked', async () => {
    listCharactersMock.mockResolvedValue({ items: [], next_cursor: null })
    renderDashboard()
    // Scope to the empty container — DashboardPage also renders a header
    // CTA with the same accessible name, and we want to assert that the
    // empty-state CTA itself wires to /characters/new.
    const empty = await screen.findByTestId('dashboard-empty')
    fireEvent.click(within(empty).getByRole('link', { name: '建立 Character' }))
    expect(await screen.findByTestId('new-character-page')).toBeInTheDocument()
  })

  it('renders a card for each returned character', async () => {
    listCharactersMock.mockResolvedValue({
      items: [
        makeCharacter({ id: 'aaaaaaaa-0000-0000-0000-000000000001', name: '小雅' }),
        makeCharacter({ id: 'aaaaaaaa-0000-0000-0000-000000000002', name: '阿傑' }),
        makeCharacter({ id: 'aaaaaaaa-0000-0000-0000-000000000003', name: '導覽-A' }),
      ],
      next_cursor: null,
    })
    renderDashboard()
    expect(await screen.findByTestId('character-grid')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '開啟角色 小雅' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '開啟角色 阿傑' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '開啟角色 導覽-A' })).toBeInTheDocument()
  })

  it('navigates to /characters/{id} when a card is clicked', async () => {
    const id = 'aaaaaaaa-0000-0000-0000-000000000abc'
    listCharactersMock.mockResolvedValue({
      items: [makeCharacter({ id, name: '導覽-B' })],
      next_cursor: null,
    })
    renderDashboard()
    const card = await screen.findByRole('link', { name: '開啟角色 導覽-B' })
    fireEvent.click(card)
    const stub = await screen.findByTestId('character-detail-stub')
    expect(stub).toHaveTextContent(`character ${id}`)
  })

  it("flags non-owner cards with the owner's name and a disabled Copy button", async () => {
    listCharactersMock.mockResolvedValue({
      items: [
        makeCharacter({
          id: 'aaaaaaaa-0000-0000-0000-000000000007',
          name: '同事的角色',
          owner: { id: TEAMMATE_ID, name: 'Mei' },
        }),
      ],
      next_cursor: null,
    })
    renderDashboard()
    expect(await screen.findByText('by Mei')).toBeInTheDocument()
    const copyButton = screen.getByRole('button', { name: '複製 同事的角色' })
    expect(copyButton).toBeDisabled()
  })

  it('shows the inline error fallback with a retry that refetches on backend failure', async () => {
    listCharactersMock.mockRejectedValueOnce(new Error('Internal Server Error'))
    renderDashboard()
    expect(await screen.findByTestId('generic-error-page')).toBeInTheDocument()

    listCharactersMock.mockResolvedValueOnce({ items: [], next_cursor: null })
    fireEvent.click(screen.getByRole('button', { name: '重試' }))

    await waitFor(() => {
      expect(screen.getByTestId('dashboard-empty')).toBeInTheDocument()
    })
    expect(listCharactersMock).toHaveBeenCalledTimes(2)
  })
})
