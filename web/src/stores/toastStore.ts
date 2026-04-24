import { toast as sonnerToast } from 'sonner'
import type { ExternalToast } from 'sonner'
import type { ReactNode } from 'react'

import { AgentError } from '@/lib/agentError'

/**
 * Thin wrapper around Sonner so callers import from a single project-owned
 * surface. Swapping Sonner out later only means changing this file.
 *
 * Layer-2 errors (async task failure) are long-lived (8s) to give users time
 * to read the expanded problem/cause/fix detail; success toasts stay at the
 * Sonner default (≈4s) per wireframes §7 visual conventions.
 */

export type ToastOptions = ExternalToast

const ERROR_TOAST_DURATION_MS = 8000

export interface AgentErrorToastOptions extends ExternalToast {
  /** Override the rendered title (defaults to the AgentError message). */
  title?: string
  /** Override the rendered description / body node. */
  description?: ReactNode
}

export const toast = {
  success(message: string, options?: ExternalToast) {
    return sonnerToast.success(message, options)
  },
  info(message: string, options?: ExternalToast) {
    return sonnerToast.info(message, options)
  },
  warning(message: string, options?: ExternalToast) {
    return sonnerToast.warning(message, options)
  },
  error(message: string, options?: ExternalToast) {
    return sonnerToast.error(message, {
      duration: ERROR_TOAST_DURATION_MS,
      ...options,
    })
  },
  /**
   * Render an `AgentError` as a Layer-2 toast. The caller owns the detail UI
   * (see `ErrorToast` composite); this helper just wires the AgentError title
   * + description into Sonner with the longer error duration.
   */
  agentError(err: AgentError, options: AgentErrorToastOptions = {}) {
    const { title, description, ...rest } = options
    return sonnerToast.error(title ?? err.message, {
      duration: ERROR_TOAST_DURATION_MS,
      description,
      ...rest,
    })
  },
  dismiss(id?: string | number) {
    return sonnerToast.dismiss(id)
  },
}

export type Toast = typeof toast
