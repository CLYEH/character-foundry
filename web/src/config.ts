/**
 * Frontend runtime config from `import.meta.env`. Centralised so non-component
 * modules (oauth-client, authStore) don't sprinkle `import.meta.env.*`
 * reads, which makes them awkward to mock in tests.
 */

export const apiBaseUrl: string = import.meta.env.VITE_API_BASE_URL ?? ''

export const authentik = {
  authorizeUrl: import.meta.env.VITE_AUTHENTIK_AUTHORIZE_URL ?? '',
  tokenUrl: import.meta.env.VITE_AUTHENTIK_TOKEN_URL ?? '',
  logoutUrl: import.meta.env.VITE_AUTHENTIK_LOGOUT_URL ?? '',
  clientId: import.meta.env.VITE_AUTHENTIK_CLIENT_ID ?? 'character-foundry-spa',
  scopes: 'openid profile email character:read character:write task:read task:cancel usage:read',
  // Slug of the Authentik OAuth Source backing the Google direct-shortcut
  // button on /login. Leave blank to hide the shortcut and force users
  // through the identification page (still backed by Authentik's Google
  // source if configured, but via two clicks instead of one).
  googleSourceSlug: import.meta.env.VITE_AUTHENTIK_GOOGLE_SOURCE_SLUG ?? 'google',
} as const
