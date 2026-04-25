import js from '@eslint/js'
import globals from 'globals'
import tseslint from 'typescript-eslint'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import prettier from 'eslint-config-prettier'

export default tseslint.config(
  { ignores: ['dist', 'node_modules', 'coverage', 'playwright-report', 'test-results'] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
    },
  },
  // Playwright config + e2e specs run under Node, not the browser. Without
  // node globals ESLint flags `process` / `__dirname` etc. The `use(page)`
  // call from Playwright fixtures trips `react-hooks/rules-of-hooks`, so
  // disable it for these files.
  {
    files: ['playwright.config.ts', 'tests/e2e/**/*.{ts,tsx}'],
    languageOptions: { globals: { ...globals.node } },
    rules: { 'react-hooks/rules-of-hooks': 'off' },
  },
  prettier,
)
