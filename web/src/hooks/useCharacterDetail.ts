import { useQuery } from '@tanstack/react-query'

import { getCharacter } from '@/api/endpoints/characters'
import { useAuthStore } from '@/stores/authStore'

export const characterDetailQueryKey = (characterId: string) =>
  ['characters', 'detail', characterId] as const

export function useCharacterDetail(characterId: string | undefined) {
  const accessToken = useAuthStore((s) => s.accessToken)
  return useQuery({
    queryKey: characterDetailQueryKey(characterId ?? ''),
    queryFn: () => getCharacter(characterId as string),
    enabled: !!accessToken && !!characterId,
    // The detail page renders an inline error fallback (NotFoundPage /
    // GenericErrorPage) so the global toast handler shouldn't double up,
    // matching the dashboard / session list query convention.
    meta: { suppressGlobalError: true },
  })
}
