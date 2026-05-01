import { useQuery } from '@tanstack/react-query'

import { listBaseMotions } from '@/api/endpoints/motions'
import { useAuthStore } from '@/stores/authStore'

export const baseMotionsQueryKey = (userId: string | undefined, baseId: string) =>
  ['motions', 'base', userId, baseId] as const

export function useBaseMotions(baseId: string | undefined) {
  const accessToken = useAuthStore((s) => s.accessToken)
  const userId = useAuthStore((s) => s.user?.id)
  return useQuery({
    queryKey: baseMotionsQueryKey(userId, baseId ?? ''),
    queryFn: () => listBaseMotions(baseId as string),
    enabled: !!accessToken && !!baseId,
    meta: { suppressGlobalError: true },
  })
}
