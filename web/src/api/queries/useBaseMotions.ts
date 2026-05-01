import { useQuery } from '@tanstack/react-query'

import { listBaseMotions } from '@/api/endpoints/motions'
import { useAuthStore } from '@/stores/authStore'

export const baseMotionsQueryKey = (baseId: string) => ['motions', 'base', baseId] as const

export function useBaseMotions(baseId: string | undefined) {
  const accessToken = useAuthStore((s) => s.accessToken)
  return useQuery({
    queryKey: baseMotionsQueryKey(baseId ?? ''),
    queryFn: () => listBaseMotions(baseId as string),
    enabled: !!accessToken && !!baseId,
    meta: { suppressGlobalError: true },
  })
}
