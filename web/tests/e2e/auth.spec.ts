import { ALICE, expect, loginAs, test } from './fixtures'

test.describe('auth flow', () => {
  test('login lands on dashboard with TopNav', async ({ page }) => {
    await loginAs(page, ALICE)

    await expect(page).toHaveURL('/')
    await expect(page.getByRole('heading', { name: '我的角色' })).toBeVisible()

    const userMenu = page.getByRole('button', { name: '使用者選單' })
    await expect(userMenu).toBeVisible()
    await expect(userMenu).toContainText(ALICE.name)
  })

  test('login page renders the three entries (Google / password / dev), no inline form', async ({
    page,
  }) => {
    // T-068 split /login into three entries. The inline email/password form
    // is still absent — the password entry routes through Authentik's
    // identification page, not a local form.
    await page.goto('/login')
    await expect(page.getByRole('button', { name: '使用 Google 登入' })).toBeVisible()
    await expect(page.getByRole('button', { name: '使用帳號密碼登入' })).toBeVisible()
    await expect(page.getByRole('link', { name: /Authentik 管理介面/ })).toHaveAttribute(
      'href',
      '/oauth/if/admin/',
    )
    // No inline credential form — the page is buttons + a dev link only.
    await expect(page.getByRole('textbox')).toHaveCount(0)
  })

  test('logout clears the session and protected routes redirect to /login', async ({
    loggedInPage: page,
  }) => {
    await page.getByRole('button', { name: '使用者選單' }).click()
    await page.getByRole('menuitem', { name: '登出' }).click()

    // Logout clears auth store; AppLayout's auth guard kicks the user back to /login.
    await page.waitForURL(/\/login/)

    // Hitting a protected route now must redirect, not render the dashboard.
    await page.goto('/')
    await expect(page).toHaveURL(/\/login/)
  })
})
