import { resolve } from 'node:path'

import { defineConfig } from 'vitest/config'

// The Preact messenger app. Built to dist/app.html (+ hashed assets) and served
// by the backend under /widget-assets/. The loader.js (host-page script) is a
// separate library build — see vite.loader.config.ts.
export default defineConfig({
  // Assets are served from the backend's /widget-assets/ mount, so every
  // generated URL in app.html must be prefixed accordingly.
  base: '/widget-assets/',
  esbuild: { jsx: 'automatic', jsxImportSource: 'preact' },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: true,
    rollupOptions: {
      input: { app: resolve(__dirname, 'app.html') },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    include: ['src/**/*.test.{ts,tsx}'],
  },
})
