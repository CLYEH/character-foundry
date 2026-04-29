import { useMutation, useQueryClient } from '@tanstack/react-query'

import {
  createAlias,
  type CreateAliasRequest,
  type CreateAliasResponse,
} from '@/api/endpoints/aliases'
import { characterDetailQueryKey } from '@/hooks/useCharacterDetail'

export function useCreateAlias(characterId: string) {
  const queryClient = useQueryClient()
  return useMutation<CreateAliasResponse, Error, CreateAliasRequest>({
    mutationFn: (body) => createAlias(characterId, body),
    onSuccess: () => {
      // The detail page is what the user lands on after the alias finishes,
      // so invalidate that query specifically. We don't blow away the whole
      // `['characters']` root because the dashboard list query under it
      // doesn't carry alias rows — refetching it just for an alias create
      // would be wasted work.
      void queryClient.invalidateQueries({ queryKey: characterDetailQueryKey(characterId) })
    },
  })
}
