import { useQuery } from '@tanstack/react-query'

import { listCharacters, type ListCharactersParams } from '@/api/endpoints/characters'
import { useAuthStore } from '@/stores/authStore'

const DEFAULT_PARAMS: ListCharactersParams = { owner_id: 'me', limit: 100 }

export function useCharacterList(params: ListCharactersParams = DEFAULT_PARAMS) {
  // Scope the cache by user id (same pattern as `useMe`) so a logout →
  // login-as-another-account inside the staleTime window can't return the
  // previous user's character list. `owner_id: 'me'` resolves on the
  // backend to the bearer's user id, so without scoping we'd hand A's
  // cached payload to B for up to staleTime ms.
  const userId = useAuthStore((s) => s.user?.id)
  const accessToken = useAuthStore((s) => s.accessToken)
  return useQuery({
    queryKey: ['characters', 'list', userId, params],
    queryFn: () => listCharacters(params),
    // Don't fire the query before login completes — without the token
    // the request would 401 and trigger the global refresh dance.
    enabled: !!accessToken,
    // The dashboard renders an inline error fallback for list failures, so
    // suppress the global toast — otherwise a 5xx shows both the inline
    // GenericErrorPage and a Sonner toast for the same error.
    meta: { suppressGlobalError: true },
  })
}
