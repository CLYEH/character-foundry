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
      // The SPA's OAuth URLs are relative (/oauth/...) on purpose to stay
      // same-origin like prod; without this entry they hit the SPA fallback.
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
