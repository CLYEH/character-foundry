import { ALICE, BOB, expect, loginAs, test } from './fixtures'
import { oauthLoginViaUi, readAuthStorage } from './fixtures/authentik'

// T-057 acceptance #2: dual-stack coexistence. While both JWT and OAuth
// paths are live (per planning/auth/open-questions.md Q4 + Q7), one
// browser context using a JWT session must not affect a parallel OAuth
// session, and vice versa. The two sessions use DIFFERENT users (Alice on
// JWT, Bob on OAuth) — a single user's tokens are not in scope here, and
// using two users keeps the assertion focused on session-vs-session
// isolation rather than user-identity tricks.
test.describe('jwt + oauth session coexistence', () => {
  test('both contexts can call the API; logout in one does not affect the other', async ({
    browser,
  }) => {
    // ── Context A: JWT login via existing fixture (writes `cf-auth` with
    //    tokenSource 'jwt') ───────────────────────────────────────────────
    const ctxJwt = await browser.newContext()
    const pageJwt = await ctxJwt.newPage()
    await loginAs(pageJwt, ALICE)
    await expect(pageJwt.getByRole('heading', { name: '我的角色' })).toBeVisible()
    const jwtStorage = await readAuthStorage(pageJwt)
    expect(jwtStorage?.state?.tokenSource).toBe('jwt')

    // ── Context B: OAuth login via Authentik flow (writes `cf-auth` with
    //    tokenSource 'oauth') ─────────────────────────────────────────────
    const ctxOauth = await browser.newContext()
    const pageOauth = await ctxOauth.newPage()
    await oauthLoginViaUi(pageOauth, BOB)
    await expect(pageOauth.getByRole('heading', { name: '我的角色' })).toBeVisible()
    const oauthStorage = await readAuthStorage(pageOauth)
    expect(oauthStorage?.state?.tokenSource).toBe('oauth')

    const jwtToken = jwtStorage?.state?.accessToken
    const oauthToken = oauthStorage?.state?.accessToken
    expect(jwtToken).toBeTruthy()
    expect(oauthToken).toBeTruthy()

    // Both tokens accepted on the same backend — the dual-stack middleware
    // dispatches by `iss` claim (T-054). Use the *first* context's request
    // pool for the JWT call and the *second* for the OAuth call so the
    // request comes from the same network identity as the browser session
    // that owns the token.
    const jwtBefore = await pageJwt.request.get('/api/v1/characters?owner_id=me&limit=1', {
      headers: { Authorization: `Bearer ${jwtToken}` },
    })
    expect(jwtBefore.status()).toBe(200)
    const oauthBefore = await pageOauth.request.get('/api/v1/characters?owner_id=me&limit=1', {
      headers: { Authorization: `Bearer ${oauthToken}` },
    })
    expect(oauthBefore.status()).toBe(200)

    // ── Logout from JWT context: must not invalidate the OAuth token ────
    await pageJwt.getByRole('button', { name: '使用者選單' }).click()
    await pageJwt.getByRole('menuitem', { name: '登出' }).click()
    await pageJwt.waitForURL(/\/login/)

    const oauthAfterJwtLogout = await pageOauth.request.get(
      '/api/v1/characters?owner_id=me&limit=1',
      { headers: { Authorization: `Bearer ${oauthToken}` } },
    )
    expect(oauthAfterJwtLogout.status()).toBe(200)

    // ── Re-login JWT context to test the reverse direction. We can't
    //    revive the original JWT — `/v1/auth/logout` revokes its refresh
    //    token (and the access token is short-lived). Mint a fresh JWT
    //    session by walking loginAs again, then logout OAuth, then verify
    //    the new JWT still works. ─────────────────────────────────────────
    await loginAs(pageJwt, ALICE)
    const jwtStorage2 = await readAuthStorage(pageJwt)
    const jwtToken2 = jwtStorage2?.state?.accessToken
    expect(jwtToken2).toBeTruthy()

    await pageOauth.getByRole('button', { name: '使用者選單' }).click()
    await pageOauth.getByRole('menuitem', { name: '登出' }).click()
    await pageOauth.waitForURL(/\/login/)

    const jwtAfterOauthLogout = await pageJwt.request.get(
      '/api/v1/characters?owner_id=me&limit=1',
      { headers: { Authorization: `Bearer ${jwtToken2}` } },
    )
    expect(jwtAfterOauthLogout.status()).toBe(200)

    await ctxJwt.close()
    await ctxOauth.close()
  })
})
