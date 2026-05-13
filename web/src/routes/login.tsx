import { useState } from 'react'
import { Navigate, useSearchParams } from 'react-router'

import { Button } from '@/components/ui/button'
import {
  buildAuthorizeUrl,
  computeChallenge,
  generateState,
  generateVerifier,
  isSafeInternalPath,
  stashPkceState,
} from '@/lib/oauth-client'
import { useAuthStore } from '@/stores/authStore'

function safeRedirectBack(raw: string | null): string {
  if (!raw) return '/'
  try {
    const decoded = decodeURIComponent(raw)
    if (isSafeInternalPath(decoded)) return decoded
  } catch {
    /* fall through */
  }
  return '/'
}

export default function LoginPage() {
  const [params] = useSearchParams()
  const redirectBack = safeRedirectBack(params.get('redirect_back'))
  const isAuthenticated = useAuthStore((s) => !!s.accessToken)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (isAuthenticated) return <Navigate to={redirectBack} replace />

  const handleSignIn = async () => {
    setStarting(true)
    setError(null)
    try {
      const verifier = generateVerifier()
      const challenge = await computeChallenge(verifier)
      const state = generateState()
      stashPkceState(verifier, state, redirectBack === '/' ? null : redirectBack)
      window.location.assign(buildAuthorizeUrl({ challenge, state }))
    } catch {
      setError('無法開始登入流程，請稍後重試')
      setStarting(false)
    }
  }

  return (
    <section className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold">登入</h1>
        <p className="text-sm text-muted-foreground">
          使用公司 Google 帳號登入 Character Foundry。
        </p>
      </header>
      <Button
        type="button"
        variant="outline"
        size="lg"
        onClick={handleSignIn}
        disabled={starting}
        aria-label="使用 Google 登入"
      >
        <GoogleMark />
        {starting ? '前往 Google 登入…' : '使用 Google 登入'}
      </Button>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
    </section>
  )
}

function GoogleMark() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className="size-4">
      <path
        fill="#EA4335"
        d="M12 10.2v3.92h5.46c-.24 1.4-1.69 4.1-5.46 4.1-3.29 0-5.97-2.72-5.97-6.07S8.71 6.07 12 6.07c1.87 0 3.13.8 3.85 1.48l2.62-2.52C16.85 3.55 14.66 2.6 12 2.6 6.81 2.6 2.6 6.81 2.6 12s4.21 9.4 9.4 9.4c5.43 0 9.03-3.81 9.03-9.18 0-.62-.07-1.09-.15-1.56H12z"
      />
    </svg>
  )
}
