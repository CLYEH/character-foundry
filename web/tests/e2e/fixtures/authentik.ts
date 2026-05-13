import type { Page } from '@playwright/test'

import { ALICE } from './index'

/**
 * Drive the OAuth Authorization Code + PKCE flow end-to-end through the
 * UI. Mirrors what the LoginPage button starts:
 *
 *   1. Navigate to /login
 *   2. Click "Sign in with Google"
 *   3. Authentik shows its default-authentication-flow stages (no upstream
 *      Google source is configured in the test blueprint, so the flow
 *      falls through to identification + password)
 *   4. The provider runs the implicit-consent authorization flow (per
 *      `cf-e2e-bootstrap.yaml`), so there is NO consent screen to click —
 *      Authentik redirects straight back to /auth/callback with the code.
 *   5. AuthCallbackPage exchanges the code, stores the token with
 *      `tokenSource: 'oauth'`, and routes to `/`.
 *
 * Selectors are pinned to Authentik's stable form field names (`uid_field`,
 * `password`) rather than label text — Authentik's flow UI is themable and
 * label text is i18n-driven, but the underlying input names have stayed
 * fixed across the 2024.x line.
 */
export async function oauthLoginViaUi(
  page: Page,
  user: { email: string; password: string } = ALICE,
): Promise<void> {
  await page.goto('/login')
  await page.getByRole('button', { name: '使用 Google 登入' }).click()

  // Authentik bounces through /oauth/application/o/authorize/ → its flow UI.
  // The flow page is rendered client-side after the JS bundle loads, so
  // wait for the identification input rather than racing the URL.
  const uidInput = page.locator('input[name="uid_field"]')
  await uidInput.waitFor({ state: 'visible', timeout: 15_000 })
  await uidInput.fill(user.email)
  // The flow's "next" button submits the identification stage; it can be
  // <button type=submit> or rendered by ak-stage-identification — match
  // by role to stay theme-agnostic.
  await page.locator('input[name="uid_field"]').press('Enter')

  const passwordInput = page.locator('input[name="password"]')
  await passwordInput.waitFor({ state: 'visible', timeout: 15_000 })
  await passwordInput.fill(user.password)
  await passwordInput.press('Enter')

  // Implicit-consent flow → redirect lands on /auth/callback first, then
  // AuthCallbackPage navigates to '/' after the token exchange. Anchor the
  // wait on the post-callback URL to avoid flaking on the brief
  // /auth/callback?code=... intermediate state.
  await page.waitForURL('/', { timeout: 30_000 })
}

interface AuthStoragePayload {
  state?: {
    tokenSource?: 'jwt' | 'oauth' | null
    accessToken?: string | null
  }
}

/** Read zustand `cf-auth` from localStorage; used by specs that assert on
 * which auth path is live on the page. */
export async function readAuthStorage(page: Page): Promise<AuthStoragePayload | null> {
  return page.evaluate(() => {
    const raw = window.localStorage.getItem('cf-auth')
    return raw ? (JSON.parse(raw) as AuthStoragePayload) : null
  })
}
