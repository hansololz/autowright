import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  // The §9.4 About page raw-imports docs/PRIVACY.md / docs/TERMS.md; Vite's
  // fs sandbox is rooted at app/ and would deny them ("Denied ID") in tests.
  server: { fs: { allow: ['..'] } },
  test: {
    environment: 'happy-dom',
    // §20: component renders run under React.StrictMode like the dev app
    setupFiles: ['tests/setup.ts'],
    include: ['tests/**/*.test.ts', 'tests/**/*.test.tsx'],
  },
})
