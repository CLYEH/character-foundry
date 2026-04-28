import { useMutation, useQueryClient } from '@tanstack/react-query'

import {
  selectBase,
  type SelectBaseRequest,
  type SelectBaseResponse,
} from '@/api/endpoints/checkpoints'
import { creationSessionQueryKey } from '@/api/queries/useCreationSession'
import { characterDetailQueryKey } from '@/hooks/useCharacterDetail'

/**
 * `POST /v1/creation-sessions/{id}/select-base` — promotes a checkpoint to the
 * Character's immutable Base. On success we invalidate:
 *   - the character list (Dashboard now needs the new `base_thumbnail_url`),
 *   - the session query (status flips to `completed`),
 *   - the character detail cache (so a subsequent navigation reads fresh).
 *
 * Errors (CONFLICT_BASE_LOCKED, VALIDATION_*) are surfaced through the
 * mutation result so the caller can route them to a toast. We deliberately
 * suppress the global mutation toast — the page wires its own (the global
 * mapping would route CONFLICT_ to inline, but there's no inline surface
 * here, so the page handles it explicitly).
 */
export function useSelectBase(sessionId: string | undefined) {
  const queryClient = useQueryClient()
  return useMutation<SelectBaseResponse, Error, SelectBaseRequest>({
    mutationFn: (body) => {
      if (!sessionId) {
        return Promise.reject(new Error('sessionId required'))
      }
      return selectBase(sessionId, body)
    },
    meta: { suppressGlobalError: true },
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: ['characters', 'list'] })
      if (sessionId) {
        void queryClient.invalidateQueries({ queryKey: creationSessionQueryKey(sessionId) })
      }
      void queryClient.invalidateQueries({
        queryKey: characterDetailQueryKey(data.character.id),
      })
    },
  })
}
