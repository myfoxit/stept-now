import { defineConfig } from 'wxt'

// Wave 7 agent B4 owns this file — expand manifest/permissions per docs/DAP2-CONTRACTS.md.
export default defineConfig({
  modules: ['@wxt-dev/module-react'],
  srcDir: 'src',
  outDir: 'dist',
  manifest: {
    name: 'Stept Recorder',
    description: 'Record, edit, preview and drive Stept product tours.',
  },
})
