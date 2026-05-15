import { ALICE, expect, test } from './fixtures'
import { oauthLoginViaUi, readAuthStorage } from './fixtures/authentik'

// T-057 acceptance #1: password fallback entry → Authentik identification
// + password → callback → Dashboard renders the character list, and
// authStore has tokenSource 'oauth'. T-068 split LoginPage into Google
// direct + password fallback + dev escape hatch; the existing happy path
// now goes through the password entry. The Google direct hop is covered
// by a separate test below that asserts the cf-google-init flow handoff
// (T-073) — the e2e blueprint doesn't seed a Google source, so we can't
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

  // T-078 behavioral contract — the SPA-local half: logout clears the
  // zustand store and lands the user on `/login`, even though the
  // Authentik session cookie deliberately survives (the shipped T-078
  // fix relaxes `default-source-authentication` so re-login through
  // the surviving session is fine; SPA logout itself stays minimal).
  //
  // ⚠ CI gap to be aware of: the original bug surfaces on the GOOGLE
  // source path (`default-source-authentication.require_unauthenticated`
  // rejecting a logged-in re-login). This spec drives the PASSWORD
  // fallback path which dispatches through `default-authentication-flow`
  // — already `authentication=none` before T-078 — so it would not
  // have caught the bug. Re-login coverage on Google lives in the
  // operator-driven CDP harness (memory
  // `feedback_verify_oauth_flow_via_cdp_before_ship`); attempting it
  // here is also brittle because the surviving Authentik session
  // causes the password path to silently skip identification +
  // password and round-trip straight back to `/auth/callback`, which
  // the standard `oauthLoginViaUi` helper can't transparently absorb.
  // Keep this test focused on the assertion the SPA side actually
  // owns: logout → /login + zustand cleared.
  test('logout lands the user on /login and clears the zustand store', async ({ page }) => {
    await oauthLoginViaUi(page, ALICE)
    await expect(page).toHaveURL('/')

    await page.getByRole('button', { name: '使用者選單' }).click()
    await page.getByRole('menuitem', { name: '登出' }).click()

    // T-078 keeps logout SPA-local: `signOutServer` revokes the
    // refresh token, the zustand store clears, and ProtectedRoute
    // bounces to `/login` on the next render. No redirect through
    // Authentik flow interface, no origin flip.
    await page.waitForURL(/\/login(\?.*)?$/, { timeout: 30_000 })

    const cleared = await readAuthStorage(page)
    expect(cleared?.state?.accessToken).toBeFalsy()
    expect(cleared?.state?.tokenSource).toBeFalsy()
  })

  // T-073 acceptance: Google entry hands off to the cf-google-init flow
  // interface — NOT the bare /source/oauth/login/google/, whose view
  // silently drops `?next=` (see buildSourceInitUrl). cf-google-init.yaml
  // is in the e2e blueprint dir, so the flow exists here and its
  // RedirectStage forwards to the source-init URL — which 404s in CI
  // since no `google` source is seeded. We assert (a) the SPA handoff URL
  // carries `next` as a PLAIN query param (T-075: pre-wrapping it in
  // `?query=` double-bundles and the executor loses the `next` key), and
  // (b) the flow forwards to /source/oauth/login/google/ — which proves
  // the blueprint actually applied (Authentik swallows blueprint errors
  // silently, so this is the only automated check that it is valid).
  test('Google entry navigates through the cf-google-init flow to source-init', async ({
    page,
  }) => {
    await page.goto('/login')

    const flowRequest = page.waitForRequest((req) => req.url().includes('/if/flow/cf-google-init/'))
    const sourceInitRequest = page.waitForRequest((req) =>
      req.url().includes('/source/oauth/login/google/'),
    )
    await page.getByRole('button', { name: '使用 Google 登入' }).click()

    // (a) SPA handoff: /if/flow/cf-google-init/?next=<authorize URL>
    const flowUrl = new URL((await flowRequest).url())
    expect(flowUrl.pathname).toContain('/if/flow/cf-google-init/')
    expect(flowUrl.searchParams.get('query')).toBeNull() // not double-bundled
    const next = flowUrl.searchParams.get('next')
    expect(next).toBeTruthy()
    expect(next).toContain('/application/o/authorize/')
    expect(next).toContain('code_challenge_method=S256')

    // (b) the flow's RedirectStage forwards to the source-init URL. The
    // RedirectStage renders an `xak-flow-redirect` challenge that the
    // flow interface follows client-side, so this shows up as a real
    // browser request — if a future Authentik turns it into a server
    // 302 that never round-trips through the browser, this waitForRequest
    // would hang until timeout rather than fail clearly.
    const sourceInitUrl = new URL((await sourceInitRequest).url())
    expect(sourceInitUrl.pathname).toContain('/source/oauth/login/google/')
  })
})
