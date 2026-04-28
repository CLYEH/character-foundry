import { useQuery } from '@tanstack/react-query'

import { listCharacters, type ListCharactersParams } from '@/api/endpoints/characters'

const DEFAULT_PARAMS: ListCharactersParams = { owner_id: 'me', limit: 100 }

export function useCharacterList(params: ListCharactersParams = DEFAULT_PARAMS) {
  return useQuery({
    queryKey: ['characters', 'list', params],
    queryFn: () => listCharacters(params),
    // The dashboard renders an inline error fallback for list failures, so
    // suppress the global toast — otherwise a 5xx shows both the inline
    // GenericErrorPage and a Sonner toast for the same error.
    meta: { suppressGlobalError: true },
  })
}
