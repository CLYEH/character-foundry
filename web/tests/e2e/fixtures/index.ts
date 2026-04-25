import { test as base, expect, type Page } from '@playwright/test'

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

export async function loginAs(page: Page, user: { email: string; password: string }) {
  await page.goto('/login')
  await page.getByLabel('Email').fill(user.email)
  await page.getByLabel('密碼').fill(user.password)
  await page.getByRole('button', { name: '登入' }).click()
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

export { expect }
