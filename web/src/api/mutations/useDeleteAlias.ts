import { useMutation, useQueryClient } from '@tanstack/react-query'

import { deleteAlias } from '@/api/endpoints/aliases'
import { aliasMotionsQueryKey } from '@/api/queries/useAliasMotions'
import { aliasListQueryKey } from '@/api/queries/useAliases'
import { characterDetailQueryKey } from '@/hooks/useCharacterDetail'

/**
 * Soft-delete an alias. The backend cascades the delete to the alias's
 * motions, so we also drop motion query caches keyed by this alias id.
 */
export function useDeleteAlias(characterId: string) {
  const qc = useQueryClient()
  return useMutation<void, Error, { aliasId: string }>({
    mutationFn: ({ aliasId }) => deleteAlias(aliasId),
    onSuccess: (_data, { aliasId }) => {
      void qc.invalidateQueries({ queryKey: aliasListQueryKey(characterId) })
      void qc.invalidateQueries({ queryKey: characterDetailQueryKey(characterId) })
      qc.removeQueries({ queryKey: aliasMotionsQueryKey(aliasId) })
    },
  })
}
