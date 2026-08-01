import { defineConfig } from 'wxt';

/** WXT build for the Stept extension (docs/DAP2-CONTRACTS.md §B4).
 *
 * Structure mirrors the ported source repo: a service-worker background, a
 * capture-phase recorder that runs in every frame at document_start, a guide
 * overlay + driver RPC island in the top frame, a selector picker, and a React
 * side panel (no popup — the panel is the session anchor and survives page
 * navigation, which a popup does not).
 *
 * `debugger` is the load-bearing permission for drive mode: it is the only way
 * an extension can dispatch TRUSTED input (`Input.dispatchMouseEvent`). Chrome
 * shows a "started debugging this browser" banner while attached; drive mode
 * degrades to synthetic events when the user dismisses/refuses it.
 */
export default defineConfig({
  modules: ['@wxt-dev/module-react'],
  srcDir: 'src',
  // `publicDir` resolves against the project root, not `srcDir`, so the icons
  // under src/public are silently dropped from the build without this.
  publicDir: 'src/public',
  // Loaded unpacked from `extension/dist/chrome-mv3` — a visible folder, not
  // WXT's hidden `.output` default (macOS hides dotfolders in the file picker).
  outDir: 'dist',
  zip: { name: 'stept-extension' },
  manifest: {
    name: 'Stept Recorder',
    description: 'Record, edit, preview and drive Stept product tours.',
    version: '0.2.0',
    minimum_chrome_version: '116',
    homepage_url: 'https://github.com/stept/stept',
    icons: { 16: 'icon/16.png', 48: 'icon/48.png', 128: 'icon/128.png' },
    permissions: [
      'storage',
      'tabs',
      'scripting',
      'sidePanel',
      'activeTab',
      'downloads',
      'webNavigation',
      'alarms',
      'debugger',
    ],
    // Extension API calls go out from the service worker; `/api/v1/*` is NOT
    // wildcard-CORS, so host access (not CORS) is what makes login work.
    host_permissions: ['<all_urls>'],
    side_panel: { default_path: 'sidepanel.html' },
    action: {
      default_title: 'Stept — record a tour',
      default_icon: { 16: 'icon/16.png', 48: 'icon/48.png', 128: 'icon/128.png' },
    },
    commands: {
      'toggle-recording': {
        suggested_key: { default: 'Ctrl+Shift+S', mac: 'Command+Shift+S' },
        description: 'Start or stop recording a Stept tour',
      },
    },
  },
});
