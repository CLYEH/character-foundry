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
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ''),
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
