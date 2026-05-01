import { useQuery } from '@tanstack/react-query'

import { listAliasMotions } from '@/api/endpoints/motions'
import { useAuthStore } from '@/stores/authStore'

export const aliasMotionsQueryKey = (aliasId: string) => ['motions', 'alias', aliasId] as const

export function useAliasMotions(aliasId: string | undefined) {
  const accessToken = useAuthStore((s) => s.accessToken)
  return useQuery({
    queryKey: aliasMotionsQueryKey(aliasId ?? ''),
    queryFn: () => listAliasMotions(aliasId as string),
    enabled: !!accessToken && !!aliasId,
    meta: { suppressGlobalError: true },
  })
}
