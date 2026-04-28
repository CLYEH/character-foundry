import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useParams } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import NewCharacterPage from '../NewCharacterPage'
import { ApiError } from '@/api/client'
import { createCharacter, type CreateCharacterResponse } from '@/api/endpoints/characters'
import { TooltipProvider } from '@/components/ui/tooltip'
import { useAuthStore } from '@/stores/authStore'

vi.mock('@/api/endpoints/characters', async () => {
  const actual = await vi.importActual<typeof import('@/api/endpoints/characters')>(
    '@/api/endpoints/characters',
  )
  return { ...actual, createCharacter: vi.fn() }
})

const createCharacterMock = vi.mocked(createCharacter)

const ME_ID = '11111111-1111-1111-1111-111111111111'
const SESSION_ID = '55555555-5555-5555-5555-555555555555'
const CHARACTER_ID = 'aaaaaaaa-0000-0000-0000-000000000111'

function makeResponse(): CreateCharacterResponse {
  return {
    character: {
      id: CHARACTER_ID,
      name: '小雅',
      slug: 'xiao-ya',
      owner: { id: ME_ID, name: 'Leo' },
      base_thumbnail_url: null,
      alias_count: 0,
      motion_count: 0,
      created_at: '2026-04-28T08:15:00Z',
      updated_at: '2026-04-28T08:15:00Z',
    },
    creation_session: {
      id: SESSION_ID,
      character_id: CHARACTER_ID,
      input_mode: 'template',
      status: 'in_progress',
      checkpoint_count: 0,
      created_at: '2026-04-28T08:15:00Z',
      completed_at: null,
    },
  }
}

function SessionStub() {
  const { id } = useParams()
  return <div data-testid="session-stub">session {id}</div>
}

function DashboardStub() {
  return <div data-testid="dashboard-stub">dashboard</div>
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
        <MemoryRouter initialEntries={['/characters/new']}>
          <Routes>
            <Route path="/" element={<DashboardStub />} />
            <Route path="/characters/new" element={<NewCharacterPage />} />
            <Route path="/characters/new/session/:id" element={<SessionStub />} />
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
        created_at: '2026-04-28T00:00:00Z',
      },
      expiresAt: Date.now() + 60_000,
    })
  })
}

describe('NewCharacterPage', () => {
  beforeEach(() => {
    seedMe()
    createCharacterMock.mockReset()
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

  it('disables the submit button until both name and mode are filled', () => {
    renderPage()
    const submit = screen.getByRole('button', { name: '建立' })
    expect(submit).toBeDisabled()

    // Name only — still disabled until a mode is chosen.
    fireEvent.change(screen.getByLabelText('先為角色取個名字'), {
      target: { value: '小雅' },
    })
    expect(submit).toBeDisabled()

    fireEvent.click(screen.getByTestId('input-mode-card-template'))
    expect(submit).toBeEnabled()
  })

  it('keeps the submit button disabled for whitespace-only names', () => {
    renderPage()
    fireEvent.click(screen.getByTestId('input-mode-card-template'))
    fireEvent.change(screen.getByLabelText('先為角色取個名字'), {
      target: { value: '   ' },
    })
    expect(screen.getByRole('button', { name: '建立' })).toBeDisabled()
  })

  it('updates the live character counter as the user types', () => {
    renderPage()
    fireEvent.change(screen.getByLabelText('先為角色取個名字'), {
      target: { value: '小雅' },
    })
    expect(screen.getByTestId('name-counter')).toHaveTextContent('2/50')
  })

  it('marks the chosen mode card as selected via aria-checked', () => {
    renderPage()
    const templateCard = screen.getByTestId('input-mode-card-template')
    const referenceCard = screen.getByTestId('input-mode-card-reference')
    expect(templateCard).toHaveAttribute('aria-checked', 'false')
    expect(referenceCard).toHaveAttribute('aria-checked', 'false')

    fireEvent.click(referenceCard)
    expect(templateCard).toHaveAttribute('aria-checked', 'false')
    expect(referenceCard).toHaveAttribute('aria-checked', 'true')
  })

  it('submits and redirects to the new creation session on success', async () => {
    createCharacterMock.mockResolvedValue(makeResponse())
    renderPage()

    fireEvent.change(screen.getByLabelText('先為角色取個名字'), {
      target: { value: '小雅' },
    })
    fireEvent.click(screen.getByTestId('input-mode-card-template'))
    fireEvent.click(screen.getByRole('button', { name: '建立' }))

    const stub = await screen.findByTestId('session-stub')
    expect(stub).toHaveTextContent(`session ${SESSION_ID}`)
    // useMutation passes a mutation-context arg after the payload, so
    // assert against the first positional arg rather than the full call.
    expect(createCharacterMock.mock.calls[0]?.[0]).toEqual({
      name: '小雅',
      input_mode: 'template',
    })
  })

  it('trims whitespace off the name before submitting', async () => {
    createCharacterMock.mockResolvedValue(makeResponse())
    renderPage()

    fireEvent.change(screen.getByLabelText('先為角色取個名字'), {
      target: { value: '  小雅  ' },
    })
    fireEvent.click(screen.getByTestId('input-mode-card-reference'))
    fireEvent.click(screen.getByRole('button', { name: '建立' }))

    await waitFor(() => {
      expect(createCharacterMock.mock.calls[0]?.[0]).toEqual({
        name: '小雅',
        input_mode: 'reference',
      })
    })
  })

  it('shows the inline duplicate-name error on CONFLICT_DUPLICATE_NAME and stays on the page', async () => {
    createCharacterMock.mockRejectedValue(
      new ApiError(409, 'CONFLICT_DUPLICATE_NAME', '此角色名稱已存在', {
        error: { code: 'CONFLICT_DUPLICATE_NAME', message: '此角色名稱已存在' },
      }),
    )
    renderPage()

    fireEvent.change(screen.getByLabelText('先為角色取個名字'), {
      target: { value: '小雅' },
    })
    fireEvent.click(screen.getByTestId('input-mode-card-template'))
    fireEvent.click(screen.getByRole('button', { name: '建立' }))

    expect(await screen.findByText('你已有一個同名角色')).toBeInTheDocument()
    expect(screen.queryByTestId('session-stub')).not.toBeInTheDocument()
  })

  it('surfaces VALIDATION_INVALID_CHARS inline on the name field', async () => {
    createCharacterMock.mockRejectedValue(
      new ApiError(400, 'VALIDATION_INVALID_CHARS', '名稱含有不允許的字元', {
        error: { code: 'VALIDATION_INVALID_CHARS', message: '名稱含有不允許的字元' },
      }),
    )
    renderPage()

    fireEvent.change(screen.getByLabelText('先為角色取個名字'), {
      target: { value: 'invalid name' },
    })
    fireEvent.click(screen.getByTestId('input-mode-card-template'))
    fireEvent.click(screen.getByRole('button', { name: '建立' }))

    expect(await screen.findByText('名稱含有不允許的字元')).toBeInTheDocument()
  })

  it('renders a Back link that points at the dashboard', () => {
    renderPage()
    const back = screen.getByRole('link', { name: '回 Dashboard' })
    expect(back).toHaveAttribute('href', '/')
  })

  it('clears the inline duplicate error when the user retypes and successfully resubmits', async () => {
    createCharacterMock.mockRejectedValueOnce(
      new ApiError(409, 'CONFLICT_DUPLICATE_NAME', '此角色名稱已存在', {
        error: { code: 'CONFLICT_DUPLICATE_NAME', message: '此角色名稱已存在' },
      }),
    )
    createCharacterMock.mockResolvedValueOnce(makeResponse())
    renderPage()

    const nameInput = screen.getByLabelText('先為角色取個名字')
    fireEvent.change(nameInput, { target: { value: '小雅' } })
    fireEvent.click(screen.getByTestId('input-mode-card-template'))
    fireEvent.click(screen.getByRole('button', { name: '建立' }))

    expect(await screen.findByText('你已有一個同名角色')).toBeInTheDocument()

    // The user types a different name; RHF clears the field error onChange
    // so the submit gate is not blocked by stale server state.
    fireEvent.change(nameInput, { target: { value: '小雅二號' } })
    await waitFor(() => {
      expect(screen.queryByText('你已有一個同名角色')).not.toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: '建立' }))
    const stub = await screen.findByTestId('session-stub')
    expect(stub).toHaveTextContent(`session ${SESSION_ID}`)
    expect(createCharacterMock).toHaveBeenCalledTimes(2)
  })
})
