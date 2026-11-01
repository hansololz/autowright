import { defineConfig } from 'vitest/config'

// §15 e2e: real Electron + real backend per test — sequential, long timeouts.
export default defineConfig({
  test: {
    environment: 'node',
    include: ['e2e/**/*.e2e.ts'],
    fileParallelism: false,
    // One retry: a real Electron launch per test occasionally dies to a
    // transient helper-process SIGABRT outside our code ("Target page,
    // context or browser has been closed"); real failures fail twice.
    retry: 1,
    testTimeout: 120_000,
    hookTimeout: 120_000,
  },
})
