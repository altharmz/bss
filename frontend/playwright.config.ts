import { defineConfig, devices } from '@playwright/test';

// The e2e smoke (make web-e2e): Playwright runs in the official image of the same version
// as @playwright/test, against the BUILT UI served by `vite preview` (the `web-preview`
// service, which proxies /tmf-api and /internal to the local app) and the seeded local
// stack (make seed). Read-only, so it can run any number of times.
export default defineConfig({
  testDir: 'e2e',
  globalSetup: './e2e/wait-for-preview.ts',
  outputDir: '/tmp/e2e-results', // nothing is written into frontend/
  timeout: 30_000,
  retries: 0,
  reporter: 'list',
  use: { baseURL: process.env.E2E_BASE_URL ?? 'http://web-preview:4173' },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 900 } },
    },
  ],
});
