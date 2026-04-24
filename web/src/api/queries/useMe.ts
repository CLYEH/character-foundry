import { useQuery } from '@tanstack/react-query'
import { getMe } from '@/api/endpoints/auth'
import { useAuthStore } from '@/stores/authStore'

export function useMe() {
  const accessToken = useAuthStore((s) => s.accessToken)
  return useQuery({
    queryKey: ['auth', 'me'],
    queryFn: getMe,
    enabled: !!accessToken,
  })
}
