import { fileURLToPath } from 'node:url'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  base: './',
  build: { outDir: 'dist' },
  // §17: the §9.4 doc modals raw-import documents from docs/ (PRIVACY.md,
  // TERMS.md, CHANGELOG.md); the dev server's default allowlist stops at app/,
  // which 403s those imports in the dev loop. The production build bundles the
  // files and needs no allowance.
  server: { fs: { allow: [fileURLToPath(new URL('..', import.meta.url))] } },
})
