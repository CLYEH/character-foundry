import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useTaskStream } from './useTaskStream'

const handlers = new Map<string, (msg: { data: string }) => void>()
const aborted: string[] = []

vi.mock('@microsoft/fetch-event-source', () => ({
  fetchEventSource: (
    url: string,
    opts: {
      onmessage: (msg: { data: string }) => void
      signal: AbortSignal
    },
  ) => {
    handlers.set(url, opts.onmessage)
    opts.signal.addEventListener('abort', () => {
      aborted.push(url)
      handlers.delete(url)
    })
    return new Promise<void>(() => {})
  },
}))

beforeEach(() => {
  handlers.clear()
  aborted.length = 0
})

afterEach(() => {
  vi.restoreAllMocks()
})

function pushTo(taskId: string, data: object) {
  const url = Array.from(handlers.keys()).find((u) => u.includes(`/tasks/${taskId}/stream`))
  if (!url) throw new Error(`no handler for ${taskId}`)
  act(() => handlers.get(url)!({ data: JSON.stringify(data) }))
}

describe('useTaskStream', () => {
  it('routes events to the correct task and exposes them via the events map', () => {
    const { result } = renderHook(() => useTaskStream())
    act(() => result.current.subscribe('A'))
    act(() => result.current.subscribe('B'))

    pushTo('A', { status: 'running', progress: 0.3 })
    pushTo('B', { status: 'queued', queue_position: 2 })

    expect(result.current.events.get('A')?.status).toBe('running')
    expect(result.current.events.get('B')?.queue_position).toBe(2)
  })

  it('fires onTerminal exactly once for completed and aborts the stream', async () => {
    const onTerminal = vi.fn()
    const { result } = renderHook(() => useTaskStream({ onTerminal }))
    act(() => result.current.subscribe('A'))

    pushTo('A', { status: 'running', progress: 0.5 })
    pushTo('A', { status: 'completed' })

    expect(onTerminal).toHaveBeenCalledTimes(1)
    expect(onTerminal).toHaveBeenLastCalledWith(
      'A',
      expect.objectContaining({ status: 'completed' }),
    )
    await waitFor(() => expect(aborted.some((u) => u.includes('/tasks/A/stream'))).toBe(true))
  })

  it('subscribe is idempotent for the same task id', () => {
    const { result } = renderHook(() => useTaskStream())
    act(() => result.current.subscribe('A'))
    act(() => result.current.subscribe('A'))
    // Only one URL should have been registered.
    const matching = Array.from(handlers.keys()).filter((u) => u.includes('/tasks/A/stream'))
    expect(matching).toHaveLength(1)
  })

  it('aborts every stream on unmount', () => {
    const { result, unmount } = renderHook(() => useTaskStream())
    act(() => result.current.subscribe('A'))
    act(() => result.current.subscribe('B'))
    unmount()
    expect(aborted.filter((u) => u.includes('/tasks/A/stream'))).toHaveLength(1)
    expect(aborted.filter((u) => u.includes('/tasks/B/stream'))).toHaveLength(1)
  })

  it('manual unsubscribe aborts the targeted stream only', () => {
    const { result } = renderHook(() => useTaskStream())
    act(() => result.current.subscribe('A'))
    act(() => result.current.subscribe('B'))
    act(() => result.current.unsubscribe('A'))
    expect(aborted.filter((u) => u.includes('/tasks/A/stream'))).toHaveLength(1)
    expect(aborted.filter((u) => u.includes('/tasks/B/stream'))).toHaveLength(0)
  })
})
