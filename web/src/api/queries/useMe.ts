import { useQuery } from '@tanstack/react-query'
import { getMe } from '@/api/endpoints/auth'
import { useAuthStore } from '@/stores/authStore'

export function useMe() {
  const accessToken = useAuthStore((s) => s.accessToken)
  // Key the cache to the user id so a quick logout → login-as-another-account
  // inside the staleTime window doesn't return the previous user's `/me`
  // payload. We key by user.id (not accessToken) so ordinary access-token
  // rotations via /v1/auth/refresh keep the cache warm.
  const userId = useAuthStore((s) => s.user?.id)
  return useQuery({
    queryKey: ['auth', 'me', userId],
    queryFn: getMe,
    enabled: !!accessToken,
  })
}
