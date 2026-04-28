import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchEventSource } from '@microsoft/fetch-event-source'

import { useAuthStore } from '@/stores/authStore'
import type { TaskEvent, TaskStatus } from '@/api/endpoints/tasks'

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? ''

const TERMINAL_STATUSES: ReadonlySet<TaskStatus> = new Set(['completed', 'failed', 'cancelled'])

class FatalSseError extends Error {}

export interface UseTaskStreamOptions {
  /**
   * Fires once per task when the stream reaches a terminal status
   * (completed / failed / cancelled). The page uses this to swap a
   * placeholder checkpoint card for the final entity from `event.result`.
   */
  onTerminal?: (taskId: string, event: TaskEvent) => void
}

export interface UseTaskStreamReturn {
  events: ReadonlyMap<string, TaskEvent>
  subscribe: (taskId: string) => void
  unsubscribe: (taskId: string) => void
}

/**
 * Manages multiple concurrent SSE subscriptions to `/v1/tasks/{id}/stream`,
 * keyed by `task_id`. Spawned per-page (not per-card) so a single state map
 * holds the latest event for every in-flight checkpoint. Subscriptions
 * self-close on terminal status and on unmount.
 *
 * `@microsoft/fetch-event-source` is required (not native `EventSource`) so
 * the JWT can ride in the `Authorization` header — see DECISIONS §3.
 */
export function useTaskStream(options: UseTaskStreamOptions = {}): UseTaskStreamReturn {
  const { onTerminal } = options
  const [events, setEvents] = useState<ReadonlyMap<string, TaskEvent>>(() => new Map())
  // Refs hold mutable state we don't want to retrigger effects with.
  const controllersRef = useRef<Map<string, AbortController>>(new Map())
  const onTerminalRef = useRef(onTerminal)

  useEffect(() => {
    onTerminalRef.current = onTerminal
  }, [onTerminal])

  const closeStream = useCallback((taskId: string) => {
    const controller = controllersRef.current.get(taskId)
    if (controller) {
      controller.abort()
      controllersRef.current.delete(taskId)
    }
  }, [])

  const subscribe = useCallback(
    (taskId: string) => {
      // Idempotent — re-subscribing the same task is a no-op rather than a
      // double connection. Pages call this from effect-friendly code paths.
      if (controllersRef.current.has(taskId)) return

      const controller = new AbortController()
      controllersRef.current.set(taskId, controller)

      const handleEvent = (event: TaskEvent) => {
        setEvents((prev) => {
          const next = new Map(prev)
          next.set(taskId, event)
          return next
        })
        if (TERMINAL_STATUSES.has(event.status)) {
          onTerminalRef.current?.(taskId, event)
          closeStream(taskId)
        }
      }

      void fetchEventSource(`${BASE_URL}/v1/tasks/${taskId}/stream`, {
        signal: controller.signal,
        // `openWhenHidden` keeps the connection alive when the tab loses
        // focus — checkpoint generation can take 30s+ and users routinely
        // task-switch while waiting.
        openWhenHidden: true,
        headers: buildAuthHeaders(),
        onmessage: (msg) => {
          if (!msg.data) return
          try {
            const parsed = JSON.parse(msg.data) as TaskEvent
            handleEvent(parsed)
          } catch {
            // Malformed payload — ignore the frame, keep the stream open.
          }
        },
        onerror: (err) => {
          // fetch-event-source retries on network blips by default; throwing
          // FatalSseError opts out of the retry loop for non-recoverable
          // failures (e.g. 401 after logout) so we don't busy-loop.
          if (err instanceof FatalSseError) throw err
        },
      }).catch(() => {
        // Swallow — the stream is gone, the UI will fall back to whatever
        // was last received and the page-level retry button is still wired
        // through `[重試]` on the failed card.
      })
    },
    [closeStream],
  )

  const unsubscribe = useCallback(
    (taskId: string) => {
      closeStream(taskId)
    },
    [closeStream],
  )

  // Abort every in-flight stream on unmount. We don't clear `events` because
  // the hook is unmounting anyway and clearing risks tearing during React 19
  // strict-mode double-invoke teardowns.
  useEffect(() => {
    const controllers = controllersRef.current
    return () => {
      for (const controller of controllers.values()) controller.abort()
      controllers.clear()
    }
  }, [])

  return { events, subscribe, unsubscribe }
}

function buildAuthHeaders(): Record<string, string> {
  const token = useAuthStore.getState().accessToken
  return token ? { Authorization: `Bearer ${token}` } : {}
}
