import { useMutation, useQueryClient } from '@tanstack/react-query'

import {
  createCharacter,
  type CreateCharacterRequest,
  type CreateCharacterResponse,
} from '@/api/endpoints/characters'

export function useCreateCharacter() {
  const queryClient = useQueryClient()
  return useMutation<CreateCharacterResponse, Error, CreateCharacterRequest>({
    mutationFn: createCharacter,
    onSuccess: () => {
      // Invalidate the dashboard list so the new character shows up when
      // the user navigates back. Scoped to the `characters` root key —
      // useCharacterList scopes the cache by user id under it.
      void queryClient.invalidateQueries({ queryKey: ['characters'] })
    },
  })
}
