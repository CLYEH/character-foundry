import { useQuery } from '@tanstack/react-query'

import { getCreationSession } from '@/api/endpoints/checkpoints'
import { useAuthStore } from '@/stores/authStore'

export const creationSessionQueryKey = (sessionId: string) =>
  ['creation-sessions', sessionId] as const

export function useCreationSession(sessionId: string | undefined) {
  const accessToken = useAuthStore((s) => s.accessToken)
  return useQuery({
    queryKey: creationSessionQueryKey(sessionId ?? ''),
    queryFn: () => getCreationSession(sessionId as string),
    enabled: !!accessToken && !!sessionId,
    // The page renders an inline error fallback for load failures so the
    // global toast handler shouldn't fire as well — same pattern as the
    // dashboard list query.
    meta: { suppressGlobalError: true },
  })
}
