import { useQuery } from '@tanstack/react-query'

import { listAliases } from '@/api/endpoints/aliases'
import { useAuthStore } from '@/stores/authStore'

/**
 * Scoped by `userId` (same convention as `useCharacterList` /
 * `useMe`) so a quick logout → login-as-another-user inside
 * `staleTime` can't return user A's alias list to user B. The user
 * id is stable for the session — `useAuthStore` is the right
 * source.
 */
export const aliasListQueryKey = (userId: string | undefined, characterId: string) =>
  ['aliases', 'list', userId, characterId] as const

export function useAliases(characterId: string | undefined) {
  const accessToken = useAuthStore((s) => s.accessToken)
  const userId = useAuthStore((s) => s.user?.id)
  return useQuery({
    queryKey: aliasListQueryKey(userId, characterId ?? ''),
    queryFn: () => listAliases(characterId as string),
    enabled: !!accessToken && !!characterId,
    meta: { suppressGlobalError: true },
  })
}
