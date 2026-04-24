import { useAuthStore } from '@/stores/authStore'

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? ''

export class ApiError extends Error {
  status: number
  code: string
  body: unknown

  constructor(status: number, code: string, message: string, body: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.body = body
  }
}

export interface ApiFetchOptions extends RequestInit {
  /**
   * Skip injecting `Authorization: Bearer` and skip the 401 → refresh → retry flow.
   * Used by endpoints that don't accept JWT (login, refresh).
   */
  skipAuth?: boolean
}

/**
 * apiFetch returns parsed JSON when the response is JSON, `undefined` on 204,
 * and the raw `Response` object for other content types so callers can pick
 * `.blob()` / `.arrayBuffer()` / `.text()` themselves (used for ZIP download,
 * signed-URL proxy, etc). Errors always throw `ApiError`.
 *
 * On 401 for protected requests, a single-flight refresh is attempted. If it
 * succeeds the original request is retried once with the new access token;
 * otherwise the auth store is cleared and the browser is navigated to /login.
 */
export async function apiFetch<T = unknown>(
  path: string,
  options: ApiFetchOptions = {},
): Promise<T> {
  const { skipAuth = false, ...init } = options
  return apiFetchInternal<T>(path, init, { skipAuth, isRetry: false })
}

async function apiFetchInternal<T>(
  path: string,
  options: RequestInit,
  ctx: { skipAuth: boolean; isRetry: boolean },
): Promise<T> {
  const headers = new Headers(options.headers)

  if (!headers.has('Content-Type') && shouldDefaultJsonContentType(options.body)) {
    headers.set('Content-Type', 'application/json')
  }

  if (!ctx.skipAuth) {
    const token = useAuthStore.getState().accessToken
    if (token) headers.set('Authorization', `Bearer ${token}`)
  }

  const res = await fetch(`${BASE_URL}${path}`, { ...options, headers })

  if (res.status === 401 && !ctx.skipAuth && !ctx.isRetry) {
    const refreshed = await attemptTokenRefresh()
    if (refreshed) {
      return apiFetchInternal<T>(path, options, { skipAuth: false, isRetry: true })
    }
    useAuthStore.getState().logout()
    authFailureRedirect.toLogin()
    // Fall through to throw below so the caller still sees the failure.
  }

  if (!res.ok) {
    const body = await readErrorBody(res)
    const err =
      typeof body === 'object' && body !== null && 'error' in body
        ? (body as { error: { code?: string; message?: string } }).error
        : undefined
    throw new ApiError(
      res.status,
      err?.code ?? `HTTP_${res.status}`,
      err?.message ?? res.statusText,
      body,
    )
  }

  if (res.status === 204 || res.status === 205) return undefined as T

  const contentType = res.headers.get('Content-Type') ?? ''
  if (contentType.includes('application/json')) {
    const text = await res.text()
    if (!text) return undefined as T
    return JSON.parse(text) as T
  }
  return res as unknown as T
}

let refreshPromise: Promise<boolean> | null = null

export async function attemptTokenRefresh(): Promise<boolean> {
  if (refreshPromise) return refreshPromise
  refreshPromise = doRefresh().finally(() => {
    refreshPromise = null
  })
  return refreshPromise
}

async function doRefresh(): Promise<boolean> {
  const refreshTokenAtStart = useAuthStore.getState().refreshToken
  if (!refreshTokenAtStart) return false
  try {
    const res = await fetch(`${BASE_URL}/v1/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshTokenAtStart }),
    })
    if (!res.ok) return false
    const data = (await res.json()) as { access_token: string; expires_in: number }
    // If the session changed while the refresh was in flight (logout, or
    // re-login as a different user), discard the result so we don't silently
    // re-authenticate the previous session. Callers treat `false` as a
    // refresh failure → they will clear auth + redirect to /login, which is
    // the right behaviour when the user has just logged out.
    if (useAuthStore.getState().refreshToken !== refreshTokenAtStart) {
      return false
    }
    useAuthStore.getState().updateAccessToken(data.access_token, data.expires_in)
    return true
  } catch {
    return false
  }
}

/**
 * Redirect seam exposed for tests: jsdom does not let us spy on
 * `window.location.assign` directly.
 */
export const authFailureRedirect = {
  toLogin(): void {
    if (typeof window === 'undefined') return
    if (window.location.pathname.startsWith('/login')) return
    const redirectBack = encodeURIComponent(window.location.pathname + window.location.search)
    window.location.assign(`/login?redirect_back=${redirectBack}`)
  },
}

function shouldDefaultJsonContentType(body: RequestInit['body']): boolean {
  if (body == null) return false
  if (typeof FormData !== 'undefined' && body instanceof FormData) return false
  if (typeof Blob !== 'undefined' && body instanceof Blob) return false
  if (typeof URLSearchParams !== 'undefined' && body instanceof URLSearchParams) return false
  if (body instanceof ArrayBuffer) return false
  if (ArrayBuffer.isView(body)) return false
  if (typeof ReadableStream !== 'undefined' && body instanceof ReadableStream) return false
  return true
}

async function readErrorBody(res: Response): Promise<unknown> {
  const text = await res.text().catch(() => '')
  if (!text) return undefined
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}
