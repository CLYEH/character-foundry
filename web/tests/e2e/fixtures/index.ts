import { test as base, expect, type Page } from '@playwright/test'

import { loginViaApi } from './characterSeed'

// Mirrors `app.cli.E2E_USERS` / `E2E_PASSWORD` (api/app/cli.py). Keep in sync
// when adding fixtures. Uses `example.com` (RFC 2606) because pydantic's
// `EmailStr` rejects `.local` as a special-use TLD.
export const ALICE = {
  email: 'test+alice@example.com',
  password: 'TestPassword123!',
  name: 'Alice',
} as const

export const BOB = {
  email: 'test+bob@example.com',
  password: 'TestPassword123!',
  name: 'Bob',
} as const

const AUTH_STORAGE_KEY = 'cf-auth'

interface JwtLoginBody {
  access_token: string
  refresh_token: string
  expires_in: number
  user: {
    id: string
    name: string
    email: string
    team_id: string
    created_at: string
  }
}

/**
 * The OAuth UI cutover (T-056) removed the email/password form, but the
 * backend keeps the JWT path live during the dual-stack window. E2E specs
 * still hit the JWT endpoint directly and seed `authStore` via
 * `localStorage` so test setup remains fast and Authentik-independent.
 * T-057 will add an OAuth-backed login fixture once the IdP is exercised in
 * CI.
 */
export async function loginAs(page: Page, user: { email: string; password: string }) {
  const response = await page.request.post('/api/v1/auth/login', {
    data: { email: user.email, password: user.password },
  })
  if (!response.ok()) {
    const body = await response.text().catch(() => '')
    throw new Error(`login failed: ${response.status()} ${response.statusText()} ${body}`)
  }
  const data = (await response.json()) as JwtLoginBody

  // Visit the SPA before touching localStorage so we're on the right origin.
  await page.goto('/login')
  // Payload shape mirrors zustand `persist` output from
  // `web/src/stores/authStore.ts` — `partialize` fields + the implicit
  // `version: 0` (no `version` configured in the store). If either side
  // changes, update both: zustand silently treats a version mismatch as a
  // stale state and rehydration goes empty, which surfaces as
  // confusing-looking redirect-loop test failures, not type errors.
  await page.evaluate(
    ({ key, payload }) => {
      window.localStorage.setItem(key, JSON.stringify(payload))
    },
    {
      key: AUTH_STORAGE_KEY,
      payload: {
        state: {
          accessToken: data.access_token,
          refreshToken: data.refresh_token,
          user: data.user,
          expiresAt: Date.now() + data.expires_in * 1000,
          tokenSource: 'jwt',
        },
        version: 0,
      },
    },
  )
  await page.goto('/')
  await page.waitForURL('/')
}

interface Fixtures {
  loggedInPage: Page
}

export const test = base.extend<Fixtures>({
  loggedInPage: async ({ page }, use) => {
    await loginAs(page, ALICE)
    await use(page)
  },
})

// Re-export so specs only import from one place.
export { expect, loginViaApi }
