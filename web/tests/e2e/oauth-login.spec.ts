import { ALICE, expect, test } from './fixtures'
import { oauthLoginViaUi, readAuthStorage } from './fixtures/authentik'

// T-057 acceptance #1: password fallback entry → Authentik identification
// + password → callback → Dashboard renders the character list, and
// authStore has tokenSource 'oauth'. T-068 split LoginPage into Google
// direct + password fallback + dev escape hatch; the existing happy path
// now goes through the password entry. The Google direct hop is covered
// by a separate test below that asserts the source-init redirect URL
// only — the e2e blueprint doesn't seed a Google source, so we can't
// follow that path through to a real callback in CI.
test.describe('oauth login smoke', () => {
  test('password entry completes the Authentik PKCE flow and lands on the dashboard', async ({
    page,
  }) => {
    await oauthLoginViaUi(page, ALICE)

    await expect(page).toHaveURL('/')
    await expect(page.getByRole('heading', { name: '我的角色' })).toBeVisible()

    // authStore must record this session as OAuth — refresh() and logout()
    // dispatch on this field, and the dual-stack coexistence spec relies on
    // it to tell the two paths apart in mixed-context scenarios.
    const storage = await readAuthStorage(page)
    expect(storage?.state?.tokenSource).toBe('oauth')
    expect(storage?.state?.accessToken).toBeTruthy()

    // Sanity-check that the OAuth token is actually accepted by the API —
    // the dashboard render alone could pass on cached state. Hitting
    // /v1/characters with the live header verifies T-054 dual-stack
    // recognises the Authentik-issued JWT.
    const response = await page.request.get('/api/v1/characters?owner_id=me&limit=1', {
      headers: { Authorization: `Bearer ${storage?.state?.accessToken}` },
    })
    expect(response.status()).toBe(200)
  })

  // T-068 acceptance: Google entry must redirect through Authentik's
  // source-init URL with the authorize URL stashed as `next`. We assert on
  // the first navigation request after the click rather than following
  // through to Google — the e2e Authentik blueprint doesn't seed a
  // `google` source, so the response would 404; the contract we care
  // about is "did the SPA hand off to the right Authentik URL".
  test('Google entry navigates through Authentik /source/oauth/login/google/', async ({ page }) => {
    await page.goto('/login')

    const sourceInitRequest = page.waitForRequest((req) =>
      req.url().includes('/source/oauth/login/google/'),
    )
    await page.getByRole('button', { name: '使用 Google 登入' }).click()
    const req = await sourceInitRequest

    const url = new URL(req.url())
    expect(url.pathname).toContain('/source/oauth/login/google/')
    const next = url.searchParams.get('next')
    expect(next).toBeTruthy()
    expect(next).toContain('/application/o/authorize/')
    expect(next).toContain('code_challenge_method=S256')
  })
})
