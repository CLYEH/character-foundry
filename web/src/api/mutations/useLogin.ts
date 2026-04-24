import { useMutation } from '@tanstack/react-query'
import { login, type LoginRequest, type LoginResponse } from '@/api/endpoints/auth'
import { useAuthStore } from '@/stores/authStore'

export function useLogin() {
  return useMutation<LoginResponse, Error, LoginRequest>({
    mutationFn: login,
    onSuccess: (data) => {
      useAuthStore.getState().login({
        accessToken: data.access_token,
        refreshToken: data.refresh_token,
        user: data.user,
        expiresIn: data.expires_in,
      })
    },
  })
}
