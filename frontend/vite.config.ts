/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// The app the dev server proxies to: `app` on the compose network (make web-dev).
const api = process.env.API_URL ?? 'http://app:8080';
// index.html's bss-build marker; deploy.sh sets the commit.
process.env.VITE_BSS_BUILD ??= 'dev';

export default defineConfig({
  plugins: [react()],
  server: {
    // all interfaces inside the container, so the published port reaches it; compose
    // publishes it on the host's 127.0.0.1:5173 only
    host: '0.0.0.0',
    port: 5173,
    strictPort: true,
    // Same origin as in production, where the shared Caddy routes these to the app.
    proxy: {
      '/tmf-api': { target: api },
      '/internal': { target: api },
    },
  },
  // make web-e2e reaches the preview as http://web-preview:4173 from the Playwright
  // container; Vite refuses unknown Host headers (DNS-rebinding protection), so allow
  // exactly that name.
  preview: { host: '0.0.0.0', port: 4173, strictPort: true, allowedHosts: ['web-preview'] },
  build: { outDir: 'dist', assetsDir: 'assets' },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.{ts,tsx}'],
    setupFiles: ['src/test/setup.ts'],
    // make progress: also list the data-ui ids passing tests saw (expectUi)
    ...(process.env.UI_TESTED_OUT
      ? { reporters: ['default', './scripts/ui-tested-reporter.mjs'] }
      : {}),
  },
});
