import { useMutation } from '@tanstack/react-query'

import {
  createAlias,
  type CreateAliasRequest,
  type CreateAliasResponse,
} from '@/api/endpoints/aliases'

/**
 * Thin POST wrapper. We deliberately don't invalidate `characterDetail`
 * on `onSuccess` — the POST returns immediately with `{ task_id, alias_id }`
 * while the worker is still queued, so the alias entity exists in the DB
 * but its image isn't generated yet. Invalidating now would refetch a
 * pre-alias snapshot and serve it stale-fresh for `staleTime` (30s),
 * causing the post-completion navigation to land on outdated detail data
 * (Codex P2 round 3).
 *
 * The page-level `handleTerminal` invalidates on the SSE `completed`
 * event instead, so the navigation back to `/characters/:id` lands on a
 * fresh fetch with the new alias visible.
 */
export function useCreateAlias(characterId: string) {
  return useMutation<CreateAliasResponse, Error, CreateAliasRequest>({
    mutationFn: (body) => createAlias(characterId, body),
  })
}
