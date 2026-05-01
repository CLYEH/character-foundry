import { expect, loginAs, test } from './fixtures'
import {
  CHARACTER_NAME_PREFIX,
  cleanupCharactersByPrefix,
  SPRINT2_USER,
  uniqueCharacterName,
} from './fixtures/characterSeed'

// Smoke coverage for Sprint 2 Flow A (planning/ux/user-flows.md §4.1):
// login → empty dashboard → create character → checkpoint generation →
// select base → character detail. Covers the happy path only — failure
// recovery, cancel, advanced prompt preview, and reference mode are out
// of scope (see ticket Not-in-scope).
//
// Pre-/post-conditions purge any T-026 character stale from a prior run
// so the empty-state assertion is deterministic. Both hooks scope by
// prefix (rather than wiping every character on the user) so a future
// `--repeat-each` run with parallel workers can't have one test's
// `beforeEach` nuke another in-flight test's character.
test.describe('character creation E2E (template)', () => {
  test.beforeEach(async ({ request }) => {
    await cleanupCharactersByPrefix(request, SPRINT2_USER, CHARACTER_NAME_PREFIX)
  })

  test.afterEach(async ({ request }) => {
    await cleanupCharactersByPrefix(request, SPRINT2_USER, CHARACTER_NAME_PREFIX)
  })

  test('happy path: create character, generate checkpoint, select as Base', async ({ page }) => {
    // Generous budget — login + form + worker pipeline + select-base + nav.
    // Stub worker usually settles in <2s but a cold-start arq + DB + storage
    // round-trip on CI runners can occasionally cross the default 30s test
    // budget; pinning explicit headroom here keeps the spec from going
    // flaky purely on infra latency.
    test.setTimeout(60_000)

    const characterName = uniqueCharacterName()

    // 1. Login as the sprint-2 fixture user.
    await loginAs(page, SPRINT2_USER)

    // 2. Dashboard empty state + CTA.
    await expect(page).toHaveURL('/')
    const empty = page.getByTestId('dashboard-empty')
    await expect(empty).toBeVisible()
    await empty.getByRole('link', { name: '建立 Character' }).click()

    // 3. Land on /characters/new.
    await expect(page).toHaveURL('/characters/new')

    // 4. Fill name, pick the template card, submit.
    await page.getByLabel('先為角色取個名字').fill(characterName)
    await page.getByTestId('input-mode-card-template').click()
    await page.getByRole('button', { name: '建立', exact: true }).click()

    // 5. Wait for the session page (URL contains the new session id).
    await page.waitForURL(/\/characters\/new\/session\/[0-9a-f-]+$/)
    await expect(page.getByRole('heading', { name: '建立角色' })).toBeVisible()

    // 6. Set a few menu fields + a freeform note. Targeting the trigger by
    //    its id (set by `<SelectTrigger id="menu-...">`) avoids the brittle
    //    label-resolution path through htmlFor on a Radix combobox.
    await page.locator('#menu-gender').click()
    await page.getByRole('option', { name: '女性' }).click()
    await page.locator('#menu-hair_style').click()
    await page.getByRole('option', { name: '長直髮' }).click()
    await page.locator('#menu-art_style').click()
    await page.getByRole('option', { name: '水墨畫' }).click()
    await page.getByLabel('自由補述').fill('穿旗袍')

    // 7. Generate.
    await page.getByRole('button', { name: '生成新候選' }).click()

    // 8. Wait for a completed checkpoint card. The card's testid is
    //    `checkpoint-card-<uuid>`, so a prefix selector picks the first
    //    one that flips to `data-status="completed"` regardless of id.
    const completedCard = page
      .locator('[data-testid^="checkpoint-card-"][data-status="completed"]')
      .first()
    await expect(completedCard).toBeVisible({ timeout: 30_000 })

    // 9. Promote the checkpoint to Base via the confirm dialog.
    await completedCard.getByRole('button', { name: '選作 Base' }).click()
    await expect(page.getByTestId('select-base-confirm')).toBeVisible()
    await page.getByTestId('select-base-confirm-action').click()

    // 10. Land on the character detail page; verify Base + empty alias /
    //     motion strips are rendered. URL ends in the new character UUID.
    await page.waitForURL(/\/characters\/[0-9a-f-]+$/)
    await expect(page.getByTestId('character-detail-name')).toHaveText(characterName)
    await expect(page.getByTestId('base-card')).toBeVisible()
    await expect(page.getByTestId('alias-empty-state')).toBeVisible()
    // T-037 replaced the legacy `motion-empty-strip` placeholder with a
    // real `MotionRow` keyed by base id — match the testid prefix
    // instead of pinning the dynamic suffix.
    await expect(page.locator('[data-testid^="motion-row-base-"]').first()).toBeVisible()
  })
})
