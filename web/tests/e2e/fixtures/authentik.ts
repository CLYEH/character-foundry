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
 * Authentik 2024.x renders the flow UI as Lit web components (`ak-stage-
 * identification`, `ak-stage-password`) and the underlying `<input>` lives
 * inside an open shadow root — attribute selectors (`input[name=...]`)
 * don't reliably pierce it. The accessibility tree, on the other hand,
 * exposes the inputs with stable English labels because we run Authentik
 * with default (en) locale in CI. If a future change adds i18n to the e2e
 * env, swap these `getByLabel` calls back to attribute selectors with the
 * shadow-piercing `>>>` combinator.
 */
export async function oauthLoginViaUi(
  page: Page,
  user: { email: string; password: string } = ALICE,
): Promise<void> {
  await page.goto('/login')
  await page.getByRole('button', { name: '使用 Google 登入' }).click()

  // Identification stage. Authentik's ak-stage-identification renders the
  // label as a sibling <span> (not <label for>), so getByLabel misses —
  // use getByRole('textbox') which reads the ARIA name from the
  // accessibility tree. The textbox aria-label is "Email or Username" in
  // the default flow.
  const uidInput = page.getByRole('textbox', { name: /email or username|email|username/i }).first()
  await uidInput.waitFor({ state: 'visible', timeout: 15_000 })
  await uidInput.fill(user.email)
  await page.getByRole('button', { name: /log in|continue|next/i }).click()

  // Password stage. Password inputs aren't role="textbox" — they expose
  // role="generic" with the aria-label set. Use getByRole('textbox') with
  // include-hidden disabled won't work; getByLabel works here because the
  // password stage uses a proper <label> wired to the input.
  const passwordInput = page.getByLabel(/password/i).first()
  await passwordInput.waitFor({ state: 'visible', timeout: 15_000 })
  await passwordInput.fill(user.password)
  await page.getByRole('button', { name: /log in|continue|sign in/i }).click()

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
