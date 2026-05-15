import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { existsSync } from 'node:fs'
import path from 'node:path'

// Dev-proxy targets depend on where `pnpm dev` runs:
//   - inside the `web` container (`docker compose up`, the default) — reach
//     peers by compose service name
//   - on the host (README "Native dev server") — reach them via the ports
//     docker-compose.override.yml publishes to localhost (api 8000, nginx 80)
// `/.dockerenv` is Docker's standard in-container marker; auto-detecting it
// keeps both workflows zero-config.
const inContainer = existsSync('/.dockerenv')
const apiProxyTarget = inContainer ? 'http://api:8000' : 'http://localhost:8000'
const oauthProxyTarget = inContainer ? 'http://nginx' : 'http://localhost'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      // Mirror prod nginx: strip the /api prefix before forwarding so that
      // VITE_API_BASE_URL=/api + backend routes /v1/* + /health lines up.
      '/api': {
        target: apiProxyTarget,
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ''),
      },
      // Mirror prod nginx: forward /oauth/ to Authentik with the prefix kept.
      // This proxy still serves the SPA's *fetch* calls to Authentik
      // (TOKEN/LOGOUT URLs, kept relative) so they stay same-origin with
      // the `:5173` SPA. The *navigation* to Authentik (AUTHORIZE_URL and
      // the cf-google-init flow URL derived from it) is ABSOLUTE to
      // Authentik's real origin since T-076 — it does NOT go through this
      // proxy. Why: Authentik flow-interface pages bootstrap with XHRs to
      // their absolute `base_url`; loading them via this proxy (`:5173`)
      // makes those XHRs cross-origin (`:5173` → `:80`) and CORS-blocked.
      // See .env.example `VITE_AUTHENTIK_AUTHORIZE_URL` for the full why.
      // changeOrigin stays false (unlike /api): Authentik derives the OAuth
      // callback redirect_uri from the inbound Host, so the Host must remain
      // the browser's real origin (localhost:5173). changeOrigin:true would
      // rewrite it to `nginx`, and Google rejects a redirect_uri on that
      // hostname. nginx routes /oauth/ by path (server_name _), not Host.
      '/oauth': {
        target: oauthProxyTarget,
        changeOrigin: false,
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    // Playwright owns tests/e2e — Vitest must not try to load specs that
    // import `@playwright/test`, which is not jsdom-compatible.
    exclude: ['node_modules', 'dist', 'tests/e2e/**'],
  },
})
