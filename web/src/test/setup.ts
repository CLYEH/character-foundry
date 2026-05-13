import '@testing-library/jest-dom/vitest'
import { vi } from 'vitest'

// Authentik OAuth endpoints — stable test values so module-level reads of
// `import.meta.env.VITE_AUTHENTIK_*` in `@/config` capture deterministic
// URLs. Tests assert against substrings (`/token/`, `/end-session/`).
vi.stubEnv('VITE_AUTHENTIK_AUTHORIZE_URL', 'https://authentik.test/application/o/authorize/')
vi.stubEnv('VITE_AUTHENTIK_TOKEN_URL', 'https://authentik.test/application/o/token/')
vi.stubEnv('VITE_AUTHENTIK_LOGOUT_URL', 'https://authentik.test/application/o/revoke/')
vi.stubEnv('VITE_AUTHENTIK_CLIENT_ID', 'character-foundry-spa')

// jsdom does not implement matchMedia, but Sonner / next-themes both call it
// when the Toaster mounts. Provide a minimal stub so rendering AppLayout in
// tests does not explode.
if (typeof window !== 'undefined' && typeof window.matchMedia !== 'function') {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  })
}
