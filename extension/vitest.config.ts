import { defineConfig } from 'vitest/config';

/** Unit tests cover the PURE modules only — compiler passes, step emission,
 * secret redaction, guide-core geometry, the driver's locate decision logic.
 * Nothing here touches `chrome.*`; entrypoints are exercised by hand + the
 * Playwright harness (out of scope this wave, see README). */
export default defineConfig({
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.ts'],
  },
});
