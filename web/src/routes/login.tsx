import { useState } from 'react'
import { Navigate, useNavigate, useSearchParams } from 'react-router'

import { ApiError } from '@/api/client'
import { useLogin } from '@/api/mutations/useLogin'
import { LoginForm } from '@/components/composite/LoginForm'
import { useAuthStore } from '@/stores/authStore'

function safeRedirectBack(raw: string | null): string {
  if (!raw) return '/'
  try {
    const decoded = decodeURIComponent(raw)
    // Only accept same-origin internal paths to avoid open-redirect.
    if (decoded.startsWith('/') && !decoded.startsWith('//')) return decoded
  } catch {
    /* fall through */
  }
  return '/'
}

export default function LoginPage() {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const redirectBack = safeRedirectBack(params.get('redirect_back'))
  const isAuthenticated = useAuthStore((s) => !!s.accessToken)
  const { mutateAsync, isPending } = useLogin()
  const [serverError, setServerError] = useState<string | null>(null)

  if (isAuthenticated) return <Navigate to={redirectBack} replace />

  return (
    <section className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold">登入</h1>
        <p className="text-sm text-muted-foreground">請使用團隊帳號登入 Character Foundry。</p>
      </header>
      <LoginForm
        submitting={isPending}
        serverError={serverError}
        onSubmit={async (values) => {
          setServerError(null)
          try {
            await mutateAsync(values)
            navigate(redirectBack, { replace: true })
          } catch (err) {
            if (err instanceof ApiError && (err.status === 401 || err.status === 400)) {
              setServerError('Email 或密碼錯誤')
            } else if (err instanceof ApiError) {
              setServerError(err.message || '登入失敗，請稍後重試')
            } else {
              setServerError('無法連線伺服器，請稍後重試')
            }
          }
        }}
      />
    </section>
  )
}
