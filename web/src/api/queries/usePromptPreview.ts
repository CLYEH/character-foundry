import { useQuery } from '@tanstack/react-query'

import {
  previewPrompt,
  type PromptPreviewRequest,
  type PromptPreviewResponse,
} from '@/api/endpoints/prompt'

/**
 * Drives the M-01 Advanced Prompt modal. The query is gated on `enabled`
 * because the LLM call costs real money — we only fire when the user opens
 * the modal (per ticket T-024 + memory `feedback_hide_implementation_from_user`).
 *
 * `meta.suppressGlobalError` keeps `VALIDATION_EMPTY_INPUT` and
 * `PROMPT_CONFLICT` off the global toast — the modal renders them inline so
 * the user sees the AgentError detail (problem / fix) in context.
 *
 * Backend Redis caches reconciler output for 24h, so close+reopen with the
 * same inputs hits cache. We don't cache on the client: each open should
 * re-fetch in case the user edited the form between opens (the modal mounts
 * fresh per open via Radix's default unmount-on-close behaviour, so a
 * different `enabled` flag isn't needed here — but `staleTime: 0` makes the
 * intent explicit if a parent ever switches to `forceMount`).
 */
export function usePromptPreview(
  request: PromptPreviewRequest,
  options: { enabled?: boolean } = {},
) {
  const enabled = options.enabled ?? true
  return useQuery<PromptPreviewResponse>({
    queryKey: ['prompt-preview', request],
    queryFn: () => previewPrompt(request),
    enabled,
    staleTime: 0,
    gcTime: 0,
    retry: false,
    meta: { suppressGlobalError: true },
  })
}
