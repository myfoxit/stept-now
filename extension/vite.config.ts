import { copyFileSync } from 'node:fs';
import { resolve } from 'node:path';

import type { Plugin } from 'vite';
import { defineConfig } from 'vitest/config';

/** Copy the static MV3 manifest into dist/ after the popup build. */
function copyManifest(): Plugin {
  return {
    name: 'stept-copy-manifest',
    closeBundle() {
      copyFileSync(
        resolve(__dirname, 'manifest.json'),
        resolve(__dirname, 'dist/manifest.json'),
      );
    },
  };
}

// Popup build (index.html -> Preact app) + manifest copy, and the vitest config.
// The content script is built separately (vite.content.config.ts) as an IIFE so
// it can be injected as a classic script via chrome.scripting.executeScript.
export default defineConfig({
  plugins: [copyManifest()],
  // Relative asset URLs so the popup loads from the extension root regardless of id.
  base: './',
  // Preact JSX via esbuild (no @preact/preset-vite needed for a build).
  esbuild: { jsx: 'automatic', jsxImportSource: 'preact' },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    target: 'es2022',
    // Avoid the inline modulepreload polyfill script (blocked by the MV3 CSP).
    modulePreload: { polyfill: false },
    rollupOptions: {
      input: resolve(__dirname, 'index.html'),
      output: {
        entryFileNames: 'assets/[name].js',
        chunkFileNames: 'assets/[name].js',
        assetFileNames: 'assets/[name].[ext]',
      },
    },
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.ts'],
  },
});
