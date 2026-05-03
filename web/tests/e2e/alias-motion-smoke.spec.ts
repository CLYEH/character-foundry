import { expect, loginAs, test } from './fixtures'
import {
  cleanupCharactersByPrefix,
  SPRINT2_USER,
  uniqueCharacterName,
} from './fixtures/characterSeed'

// M3 milestone smoke (T-041): login → create character → checkpoint →
// select Base → add alias (text-only) → generate base preset_wave motion
// → open lightbox. Backend runs in stub mode (AI_STUB_MODE=true), so the
// AI calls return bundled fixtures and the full pipeline settles in
// well under the spec's 60s ceiling.
//
// We piggyback on T-026's `E2E-T026-` character prefix for cleanup so
// both specs hit the same idempotent purge in beforeEach / afterEach
// and share the existing fixture infrastructure. A dedicated prefix
// would also work but would mean two cleanup paths to maintain.
test.describe('alias + motion E2E (M3 smoke)', () => {
  test.beforeEach(async ({ request }) => {
    await cleanupCharactersByPrefix(request, SPRINT2_USER, 'E2E-T041-')
  })

  test.afterEach(async ({ request }) => {
    await cleanupCharactersByPrefix(request, SPRINT2_USER, 'E2E-T041-')
  })

  test('happy path: create character → alias → preset_wave motion → lightbox', async ({ page }) => {
    // Login + character + checkpoint + alias generation + motion generation
    // each touch the worker pipeline once or twice. Stub mode usually
    // settles end-to-end in <10s but cold-start arq + DB + storage on a
    // CI runner can creep up — the explicit 60s ceiling keeps the spec
    // from going flaky purely on infra latency. T-026 uses the same
    // budget for the comparable creation-only flow.
    test.setTimeout(60_000)

    const characterName = uniqueCharacterName('E2E-T041-')
    const aliasName = `紅旗袍-${Date.now().toString(36)}`

    // 1. Login.
    await loginAs(page, SPRINT2_USER)

    // 2. Dashboard → 建立 Character.
    await expect(page).toHaveURL('/')
    const empty = page.getByTestId('dashboard-empty')
    if (await empty.isVisible().catch(() => false)) {
      await empty.getByRole('link', { name: '建立 Character' }).click()
    } else {
      // T-026 cleanup may have left the user with other characters from
      // a parallel run; the empty-state CTA isn't the only entry into
      // /characters/new. Falling back to the TopNav-style direct nav
      // keeps the smoke test stable regardless of dashboard state.
      await page.goto('/characters/new')
    }

    // 3. Pick template mode + name + submit.
    await expect(page).toHaveURL('/characters/new')
    await page.getByLabel('先為角色取個名字').fill(characterName)
    await page.getByTestId('input-mode-card-template').click()
    await page.getByRole('button', { name: '建立', exact: true }).click()

    // 4. Creation session → fill the minimum menu fields + a freeform
    //    note, generate one checkpoint, promote it to Base. Mirrors
    //    T-026's flow so the underlying assertions are battle-tested.
    await page.waitForURL(/\/characters\/new\/session\/[0-9a-f-]+$/)
    await page.locator('#menu-gender').click()
    await page.getByRole('option', { name: '女性' }).click()
    await page.locator('#menu-hair_style').click()
    await page.getByRole('option', { name: '長直髮' }).click()
    await page.locator('#menu-art_style').click()
    await page.getByRole('option', { name: '水墨畫' }).click()
    await page.getByLabel('自由補述').fill('穿旗袍')
    await page.getByRole('button', { name: '生成新候選' }).click()

    const completedCard = page
      .locator('[data-testid^="checkpoint-card-"][data-status="completed"]')
      .first()
    await expect(completedCard).toBeVisible({ timeout: 30_000 })
    await completedCard.getByRole('button', { name: '選作 Base' }).click()
    await expect(page.getByTestId('select-base-confirm')).toBeVisible()
    await page.getByTestId('select-base-confirm-action').click()

    // 5. Land on character detail with Base + empty Aliases section.
    await page.waitForURL(/\/characters\/[0-9a-f-]+$/)
    await expect(page.getByTestId('character-detail-name')).toHaveText(characterName)
    await expect(page.getByTestId('base-card')).toBeVisible()

    // 6. Wait for aliases section to render (async fetch resolves to
    //    empty), then click the empty-state CTA → alias edit page.
    await expect(page.getByTestId('alias-empty-state')).toBeVisible()
    await page.getByTestId('alias-empty-create-cta').click()
    await page.waitForURL(/\/characters\/[0-9a-f-]+\/aliases\/new$/)

    // 7. Fill alias name + freeform_note (text-only mode), submit, wait
    //    for SSE-driven auto-nav back to detail. The text section is
    //    pre-toggled in AliasEditBody, so we only need to drop the
    //    freeform note + name.
    await page.getByLabel('Alias 名稱').fill(aliasName)
    await page.getByLabel('Alias 補述內容').fill('紅色旗袍版本')

    // Couple the click with the alias-create POST so a stale-closure
    // submit handler surfaces here, not as a 30s URL wait. The response
    // carries `task_id` + `alias_id`; the worker SSE then drives the
    // task to `completed` and AliasEditPage.handleTerminal navigates
    // back to /characters/:id.
    const [createResponse] = await Promise.all([
      page.waitForResponse(
        (resp) =>
          resp.request().method() === 'POST' &&
          /\/v1\/characters\/[0-9a-f-]+\/aliases$/.test(resp.url()),
      ),
      page.getByTestId('alias-submit').click(),
    ])
    expect(createResponse.status()).toBe(202)

    // Auto-nav back to /characters/:id on SSE `completed`. Detail page
    // invalidates its query before nav, but the refetch may land a
    // tick after the URL flips — wait for the alias row itself (not
    // `aliases-list`, which only renders once items are non-empty).
    await page.waitForURL(/\/characters\/[0-9a-f-]+$/, { timeout: 30_000 })
    await expect(
      page.locator('[data-testid^="alias-row-name-"]', { hasText: aliasName }),
    ).toBeVisible({ timeout: 10_000 })

    // 8. Click the base's preset_wave empty cell → wait for the cell to
    //    flip to `completed` (driven by SSE → motions list refetch). The
    //    completed cell's testid embeds the motion id, so we match by
    //    prefix + the cell's `data-slot-id` semantic neighbour: the
    //    initial empty cell carries `data-slot-id="preset_wave"` and on
    //    completion is replaced by a `motion-cell-completed-<motion-id>`.
    const waveTrigger = page
      .locator('[data-testid^="motion-row-base-"]')
      .first()
      .locator('[data-testid="motion-cell-empty-preset_wave"]')
    await expect(waveTrigger).toBeVisible()
    await waveTrigger.click()

    const completedWaveCell = page
      .locator('[data-testid^="motion-row-base-"]')
      .first()
      .locator('[data-testid^="motion-cell-completed-"]')
      .first()
    await expect(completedWaveCell).toBeVisible({ timeout: 30_000 })

    // 9. Open the lightbox by clicking the completed cell, verify a
    //    <video> element renders with a non-empty src. We don't try to
    //    play the video — Phase 1 only needs the URL to be wired
    //    through the storage backend, which the `src` attribute proves.
    await completedWaveCell.click()
    await expect(page.getByTestId('motion-lightbox')).toBeVisible()
    const video = page.getByTestId('motion-lightbox-video')
    await expect(video).toBeVisible()
    const src = await video.getAttribute('src')
    expect(src).toBeTruthy()
    expect(src ?? '').not.toEqual('')
  })
})
