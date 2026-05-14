import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'node:path'

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
      // Target is the docker service name — `pnpm dev` runs inside the `web`
      // container, where `localhost` is the container itself, not `api`.
      '/api': {
        target: 'http://api:8000',
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
        target: 'http://nginx',
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
