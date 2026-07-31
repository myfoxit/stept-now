import { resolve } from 'node:path';

import { defineConfig } from 'vite';

// Builds the content script as a single self-contained IIFE at dist/content.js,
// so it can be injected as a classic script via chrome.scripting.executeScript.
// emptyOutDir is false so it is added alongside the popup build output.
export default defineConfig({
  build: {
    outDir: 'dist',
    emptyOutDir: false,
    target: 'es2022',
    lib: {
      entry: resolve(__dirname, 'src/content.ts'),
      formats: ['iife'],
      name: '__steptRecorder',
      fileName: () => 'content.js',
    },
    rollupOptions: {
      output: { entryFileNames: 'content.js', extend: true },
    },
  },
});
