import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ErrorBoundary } from './ErrorBoundary'
import { ApiError } from '@/api/client'

function Thrower({ error }: { error: unknown }): null {
  throw error
}

function renderWithRouter(node: React.ReactNode) {
  return render(<MemoryRouter>{node}</MemoryRouter>)
}

describe('ErrorBoundary', () => {
  let errorSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    // react-error-boundary logs caught errors via console.error; silence it
    // so the test output stays readable. We still assert on the onError prop.
    errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
  })

  afterEach(() => {
    errorSpy.mockRestore()
  })

  it('catches a render error and renders the generic error page', () => {
    renderWithRouter(
      <ErrorBoundary>
        <Thrower error={new Error('render blew up')} />
      </ErrorBoundary>,
    )

    expect(screen.getByTestId('generic-error-page')).toBeInTheDocument()
    expect(screen.getByText('render blew up')).toBeInTheDocument()
  })

  it('renders the NotFound page when caught error is a NOT_FOUND_* AgentError', () => {
    const notFoundApiErr = new ApiError(404, 'NOT_FOUND_CHARACTER', '找不到角色', {
      error: { code: 'NOT_FOUND_CHARACTER', message: '找不到角色' },
    })

    renderWithRouter(
      <ErrorBoundary>
        <Thrower error={notFoundApiErr} />
      </ErrorBoundary>,
    )

    expect(screen.getByTestId('not-found-page')).toBeInTheDocument()
  })

  it('renders the connection error page for TypeError fetch failures', () => {
    renderWithRouter(
      <ErrorBoundary>
        <Thrower error={new TypeError('Failed to fetch')} />
      </ErrorBoundary>,
    )

    expect(screen.getByTestId('connection-error-page')).toBeInTheDocument()
  })

  it('invokes onError with the caught error and component stack', () => {
    const onError = vi.fn()
    renderWithRouter(
      <ErrorBoundary onError={onError}>
        <Thrower error={new Error('boom')} />
      </ErrorBoundary>,
    )

    expect(onError).toHaveBeenCalledTimes(1)
    const [err, info] = onError.mock.calls[0]
    expect(err).toBeInstanceOf(Error)
    expect((err as Error).message).toBe('boom')
    expect(info.componentStack).toBeTruthy()
  })

  it('resets and re-renders children when the retry action is clicked', () => {
    let shouldThrow = true
    function Flaky() {
      if (shouldThrow) throw new Error('first render fails')
      return <div>recovered</div>
    }

    renderWithRouter(
      <ErrorBoundary>
        <Flaky />
      </ErrorBoundary>,
    )

    expect(screen.getByTestId('generic-error-page')).toBeInTheDocument()

    shouldThrow = false
    fireEvent.click(screen.getByRole('button', { name: '重試' }))

    expect(screen.getByText('recovered')).toBeInTheDocument()
  })

  it('renders a custom fallback when provided', () => {
    renderWithRouter(
      <ErrorBoundary fallback={({ error }) => <div>caught: {(error as Error).message}</div>}>
        <Thrower error={new Error('custom path')} />
      </ErrorBoundary>,
    )

    expect(screen.getByText('caught: custom path')).toBeInTheDocument()
  })
})
