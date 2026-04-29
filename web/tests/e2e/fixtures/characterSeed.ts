import type { APIRequestContext, APIResponse } from '@playwright/test'

// Fixed sprint-2 identity, kept distinct from Alice/Bob so character /
// checkpoint pollution from creation specs cannot bleed into the auth-only
// specs anchored on Alice. Mirrors `app.cli.E2E_USERS` (api/app/cli.py).
export const SPRINT2_USER = {
  email: 'test+sprint2@example.com',
  password: 'TestPassword123!',
  name: 'Sprint2',
} as const

// Backend mounts under `/api` (vite dev proxy + nginx in CI both strip the
// prefix before forwarding). Playwright's `request` context honours the
// project `baseURL`, so a relative path is enough.
const API_PREFIX = '/api'

interface CharacterSummary {
  id: string
  name: string
}

async function expectOk(response: APIResponse, action: string): Promise<void> {
  if (!response.ok()) {
    const body = await response.text().catch(() => '')
    throw new Error(`${action} failed: ${response.status()} ${response.statusText()} ${body}`)
  }
}

export async function loginViaApi(
  request: APIRequestContext,
  user: { email: string; password: string },
): Promise<string> {
  const response = await request.post(`${API_PREFIX}/v1/auth/login`, {
    data: { email: user.email, password: user.password },
  })
  await expectOk(response, 'login')
  const body = (await response.json()) as { access_token: string }
  return body.access_token
}

async function listOwnedCharacters(
  request: APIRequestContext,
  accessToken: string,
): Promise<CharacterSummary[]> {
  // `owner_id=me` scopes to the caller; `limit=100` is the API max so a
  // single call is enough for cleanup as long as we don't leave more than
  // 100 stale characters behind — and if we do, the next run will mop up
  // the rest because cleanup runs every test.
  const response = await request.get(`${API_PREFIX}/v1/characters?owner_id=me&limit=100`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  })
  await expectOk(response, 'list characters')
  const body = (await response.json()) as { items: CharacterSummary[] }
  return body.items
}

async function deleteCharacter(
  request: APIRequestContext,
  accessToken: string,
  characterId: string,
): Promise<void> {
  const response = await request.delete(`${API_PREFIX}/v1/characters/${characterId}`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  })
  // 404 is fine — the character may have already been removed by a parallel
  // cleanup or never finished creating. Treat anything else non-2xx as fatal
  // so silent leaks don't accumulate.
  if (response.status() === 404) return
  await expectOk(response, `delete character ${characterId}`)
}

/**
 * Idempotent cleanup of every character owned by `user` whose name starts
 * with `namePrefix`. Restricting by prefix keeps the helper safe to call
 * from afterEach without nuking unrelated fixtures (Bob, Alice, manual dev
 * data on a shared local DB).
 */
export async function cleanupCharactersByPrefix(
  request: APIRequestContext,
  user: { email: string; password: string },
  namePrefix: string,
): Promise<void> {
  const accessToken = await loginViaApi(request, user)
  const characters = await listOwnedCharacters(request, accessToken)
  for (const character of characters) {
    if (character.name.startsWith(namePrefix)) {
      await deleteCharacter(request, accessToken, character.id)
    }
  }
}

export const CHARACTER_NAME_PREFIX = 'E2E-T026-'

/**
 * Build a unique character name for a single test run. The prefix is shared
 * across runs so cleanup-by-prefix can find them, but the suffix collides
 * neither with prior runs (Date.now) nor with parallel workers (random).
 */
export function uniqueCharacterName(prefix = CHARACTER_NAME_PREFIX): string {
  const stamp = Date.now().toString(36)
  const random = Math.random().toString(36).slice(2, 8)
  return `${prefix}${stamp}-${random}`
}
