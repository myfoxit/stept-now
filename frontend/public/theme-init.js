// Apply the persisted theme before first paint to avoid a flash. Kept as an
// external file (not an inline <script>) so the dashboard's Content-Security-
// Policy can be `script-src 'self'` with no 'unsafe-inline'. See deploy/nginx.conf.
try {
  var theme = localStorage.getItem('stept-theme')
  var dark =
    theme === 'dark' ||
    (theme !== 'light' && window.matchMedia('(prefers-color-scheme: dark)').matches)
  document.documentElement.classList.toggle('dark', dark)
} catch (e) {
  /* private mode */
}
