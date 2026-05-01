import { useQuery } from '@tanstack/react-query'

import { listAliasMotions } from '@/api/endpoints/motions'
import { useAuthStore } from '@/stores/authStore'

export const aliasMotionsQueryKey = (userId: string | undefined, aliasId: string) =>
  ['motions', 'alias', userId, aliasId] as const

export function useAliasMotions(aliasId: string | undefined) {
  const accessToken = useAuthStore((s) => s.accessToken)
  const userId = useAuthStore((s) => s.user?.id)
  return useQuery({
    queryKey: aliasMotionsQueryKey(userId, aliasId ?? ''),
    queryFn: () => listAliasMotions(aliasId as string),
    enabled: !!accessToken && !!aliasId,
    meta: { suppressGlobalError: true },
  })
}
