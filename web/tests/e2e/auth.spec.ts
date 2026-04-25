import { ALICE, expect, loginAs, test } from './fixtures'

test.describe('auth flow', () => {
  test('login with valid credentials lands on dashboard with TopNav', async ({ page }) => {
    await loginAs(page, ALICE)

    await expect(page).toHaveURL('/')
    await expect(page.getByRole('heading', { name: 'Hello' })).toBeVisible()

    const userMenu = page.getByRole('button', { name: '使用者選單' })
    await expect(userMenu).toBeVisible()
    await expect(userMenu).toContainText(ALICE.name)
  })

  test('login with wrong password shows inline error and stays on /login', async ({ page }) => {
    await page.goto('/login')
    await page.getByLabel('Email').fill(ALICE.email)
    await page.getByLabel('密碼').fill('WrongPassword!')
    await page.getByRole('button', { name: '登入' }).click()

    await expect(page.getByText('Email 或密碼錯誤')).toBeVisible()
    await expect(page).toHaveURL(/\/login/)
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
