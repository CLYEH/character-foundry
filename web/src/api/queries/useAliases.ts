import { useQuery } from '@tanstack/react-query'

import { listAliases } from '@/api/endpoints/aliases'
import { useAuthStore } from '@/stores/authStore'

export const aliasListQueryKey = (characterId: string) => ['aliases', 'list', characterId] as const

export function useAliases(characterId: string | undefined) {
  const accessToken = useAuthStore((s) => s.accessToken)
  return useQuery({
    queryKey: aliasListQueryKey(characterId ?? ''),
    queryFn: () => listAliases(characterId as string),
    enabled: !!accessToken && !!characterId,
    meta: { suppressGlobalError: true },
  })
}
