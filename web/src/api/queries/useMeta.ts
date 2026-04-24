import { useQuery } from '@tanstack/react-query'

import { getMeta } from '@/api/endpoints/meta'

const REFETCH_MS = 60_000

export function useMeta() {
  return useQuery({
    queryKey: ['meta'],
    queryFn: getMeta,
    staleTime: REFETCH_MS,
    refetchInterval: REFETCH_MS,
  })
}
