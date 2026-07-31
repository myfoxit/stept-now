import { resolve } from 'node:path'

import { defineConfig } from 'vite'

// The embeddable loader script. A single, dependency-free IIFE emitted to
// dist/loader.js — this is the `<script src=".../widget-assets/loader.js">` a
// site drops in. Runs AFTER the app build (emptyOutDir: false) so it lands in
// the same dist/ next to app.html without wiping it.
export default defineConfig({
  esbuild: { jsx: 'automatic', jsxImportSource: 'preact' },
  build: {
    outDir: 'dist',
    emptyOutDir: false,
    lib: {
      entry: resolve(__dirname, 'src/loader.ts'),
      formats: ['iife'],
      name: 'SteptLoader',
      fileName: () => 'loader.js',
    },
    // Keep it tiny; no code-splitting for a single embed script.
    rollupOptions: {
      output: { inlineDynamicImports: true },
    },
    minify: 'esbuild',
  },
})
