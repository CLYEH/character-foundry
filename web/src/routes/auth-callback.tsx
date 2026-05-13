import { useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router'

import { getMe } from '@/api/endpoints/auth'
import { Button } from '@/components/ui/button'
import {
  consumePkceState,
  exchangeCodeForToken,
  isSafeInternalPath,
  OauthError,
  REDIRECT_PATH,
} from '@/lib/oauth-client'
import { useAuthStore } from '@/stores/authStore'

type Status = 'pending' | 'error'

export default function AuthCallbackPage() {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const [status, setStatus] = useState<Status>('pending')
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const ranRef = useRef(false)

  useEffect(() => {
    // React 18 strict-mode double-invokes effects in dev. The OAuth code is
    // single-use, so guard against the second invocation eating it.
    if (ranRef.current) return
    ranRef.current = true

    const code = params.get('code')
    const state = params.get('state')
    const oauthError = params.get('error')
    const { verifier, state: storedState, redirectBack } = consumePkceState()

    if (oauthError) {
      setStatus('error')
      setErrorMessage(params.get('error_description') ?? oauthError)
      return
    }

    if (!code || !state || !verifier || !storedState) {
      setStatus('error')
      setErrorMessage('登入回呼缺少必要參數，請重新登入')
      return
    }

    if (state !== storedState) {
      setStatus('error')
      setErrorMessage('登入狀態不符，可能遭到竄改，請重新登入')
      return
    }

    void (async () => {
      try {
        const token = await exchangeCodeForToken({ code, verifier })
        const newRefresh = token.refresh_token ?? null
        useAuthStore.setState({
          accessToken: token.access_token,
          refreshToken: newRefresh,
          idToken: token.id_token ?? null,
          tokenSource: 'oauth',
          expiresAt: Date.now() + token.expires_in * 1000,
        })
        const me = await getMe()
        // If something cleared the session (a parallel logout, storage tab
        // sync) between the two writes, don't resurrect a half-populated one.
        if (useAuthStore.getState().refreshToken !== newRefresh) return
        useAuthStore.setState({ user: me.user })
        // Re-validate `redirectBack` even though only LoginPage writes it
        // today — defense in depth against a poisoned sessionStorage entry
        // from a future call site or an XSS payload.
        const target = isSafeInternalPath(redirectBack) ? redirectBack : '/'
        navigate(target, { replace: true })
      } catch (err) {
        useAuthStore.getState().logout()
        setStatus('error')
        if (err instanceof OauthError) {
          setErrorMessage(err.message || err.code)
        } else {
          setErrorMessage('無法完成登入，請稍後重試')
        }
      }
    })()
  }, [params, navigate])

  if (status === 'pending') {
    return (
      <section className="flex flex-col gap-2" aria-live="polite">
        <h1 className="text-2xl font-semibold">登入中…</h1>
        <p className="text-sm text-muted-foreground">正在完成 Google 登入流程。</p>
      </section>
    )
  }

  return (
    <section className="flex flex-col gap-4">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold">登入失敗</h1>
        <p role="alert" className="text-sm text-destructive">
          {errorMessage ?? '未知錯誤'}
        </p>
      </header>
      <Button
        type="button"
        onClick={() => {
          // Clear the failed `?code=...` so a refresh of /login doesn't trip
          // the auth-callback guard again.
          if (window.location.pathname === REDIRECT_PATH) {
            window.history.replaceState({}, '', '/login')
          }
          navigate('/login', { replace: true })
        }}
      >
        重試
      </Button>
    </section>
  )
}
