import { ALICE, expect, test } from './fixtures'
import { oauthLoginViaUi, readAuthStorage } from './fixtures/authentik'

// T-057 acceptance #1: "Sign in with Google" → Authentik consent → callback
// → Dashboard renders the character list, and authStore has tokenSource
// 'oauth'. The Google upstream is replaced by Authentik's default
// identification + password stages — see `infra/authentik/blueprints/
// cf-e2e-bootstrap.yaml` for why and what's seeded.
test.describe('oauth login smoke', () => {
  test('completes the Authentik PKCE flow and lands on the dashboard', async ({ page }) => {
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
})
