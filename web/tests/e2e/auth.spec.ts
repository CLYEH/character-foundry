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

  test('login page renders only the Google sign-in button', async ({ page }) => {
    await page.goto('/login')
    await expect(page.getByRole('button', { name: '使用 Google 登入' })).toBeVisible()
    await expect(page.getByLabel('Email')).toHaveCount(0)
    await expect(page.getByLabel('密碼')).toHaveCount(0)
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
