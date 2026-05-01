import { useMutation, useQueryClient } from '@tanstack/react-query'

import { patchAlias, type AliasResponse, type PatchAliasRequest } from '@/api/endpoints/aliases'
import { aliasListQueryKey } from '@/api/queries/useAliases'
import { characterDetailQueryKey } from '@/hooks/useCharacterDetail'

/**
 * Rename an alias. Invalidates the per-character alias list (so cards
 * refresh in place) plus the character detail (which carries
 * `motions_summary` keyed by alias_id but not the alias name itself —
 * still cheap to re-fetch and keeps the two views consistent).
 */
export function useRenameAlias(characterId: string) {
  const qc = useQueryClient()
  return useMutation<AliasResponse, Error, { aliasId: string; body: PatchAliasRequest }>({
    mutationFn: ({ aliasId, body }) => patchAlias(aliasId, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: aliasListQueryKey(characterId) })
      void qc.invalidateQueries({ queryKey: characterDetailQueryKey(characterId) })
    },
  })
}
