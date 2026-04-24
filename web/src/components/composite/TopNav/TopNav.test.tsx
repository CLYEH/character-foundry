import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { TopNav } from './TopNav'
import { AUTH_STORAGE_KEY, useAuthStore } from '@/stores/authStore'

function seedAuthUser(name: string, email: string) {
  act(() => {
    useAuthStore.setState({
      accessToken: 'a',
      refreshToken: 'r',
      user: {
        id: '11111111-1111-1111-1111-111111111111',
        name,
        email,
        team_id: '22222222-2222-2222-2222-222222222222',
        created_at: '2026-04-24T00:00:00Z',
      },
      expiresAt: Date.now() + 60_000,
    })
  })
}

async function renderTopNav() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchInterval: false } },
  })
  let utils!: ReturnType<typeof render>
  await act(async () => {
    utils = render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <TopNav />
        </MemoryRouter>
      </QueryClientProvider>,
    )
    // Radix DropdownMenu schedules a post-mount state update (id generation
    // + collection registration). Flushing one microtask here keeps that
    // update inside the wrapping act() so tests don't emit act() warnings.
    await Promise.resolve()
  })
  return utils
}

describe('TopNav', () => {
  beforeEach(() => {
    useAuthStore.setState({
      accessToken: null,
      refreshToken: null,
      user: null,
      expiresAt: null,
    })
    localStorage.removeItem(AUTH_STORAGE_KEY)
  })

  afterEach(() => {
    useAuthStore.setState({
      accessToken: null,
      refreshToken: null,
      user: null,
      expiresAt: null,
    })
  })

  it('renders the logo link back to the home route', async () => {
    seedAuthUser('Alice', 'alice@internal.com')
    await renderTopNav()
    const logo = screen.getByRole('link', { name: /回首頁/ })
    expect(logo).toHaveAttribute('href', '/')
  })

  it('renders the search input as a placeholder (stub, disabled)', async () => {
    seedAuthUser('Alice', 'alice@internal.com')
    await renderTopNav()
    const search = screen.getByRole('searchbox', { name: /搜尋角色/ })
    expect(search).toBeInTheDocument()
    expect(search).toBeDisabled()
  })

  it('renders the usage placeholder with dashes', async () => {
    seedAuthUser('Alice', 'alice@internal.com')
    await renderTopNav()
    const usage = screen.getByLabelText('本月用量')
    expect(usage).toHaveTextContent('--')
  })

  it("shows the logged-in user's name in the user menu trigger", async () => {
    seedAuthUser('Alice', 'alice@internal.com')
    await renderTopNav()
    const trigger = screen.getByRole('button', { name: /使用者選單/ })
    expect(trigger).toHaveTextContent('Alice')
  })

  it('hides the user menu when there is no logged-in user', async () => {
    // No auth seeded — mimics a transient state, UserMenu should opt out.
    await renderTopNav()
    expect(screen.queryByRole('button', { name: /使用者選單/ })).not.toBeInTheDocument()
  })
})
