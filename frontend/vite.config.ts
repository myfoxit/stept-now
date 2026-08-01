import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  server: {
    port: 5273,
    proxy: (() => {
      const backend = process.env.STEPT_BACKEND_ORIGIN ?? 'http://localhost:8600'
      const wsBackend = backend.replace(/^http/, 'ws')
      return {
        '/api': { target: backend, changeOrigin: true },
        '/ws': { target: wsBackend, ws: true },
        '/portal': { target: backend, changeOrigin: true },
        '/widget-assets': { target: backend, changeOrigin: true },
        '/extension-assets': { target: backend, changeOrigin: true },
      }
    })(),
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
    chunkSizeWarningLimit: 1200,
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
  },
})
