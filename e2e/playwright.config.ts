import { defineConfig, devices } from '@playwright/test'

/**
 * E2E stack: real backend (SQLite + in-memory queue/pubsub + mock AI, seeded)
 * on :8611 + frontend dev server proxying to it on :5281. Zero external deps.
 */

const BACKEND_PORT = 8611
const FRONTEND_PORT = 5281

const backendEnv = [
  `STEPT_ENV=dev`,
  `STEPT_SECRET_KEY=e2e-secret-key-for-tests-only`,
  `STEPT_DATABASE_URL=sqlite+aiosqlite:///./e2e.db`,
  `STEPT_STORAGE_DIR=./e2e-uploads`,
  `STEPT_PUBLIC_BASE_URL=http://localhost:${BACKEND_PORT}`,
  `STEPT_APP_BASE_URL=http://localhost:${FRONTEND_PORT}`,
  `STEPT_RATE_LIMIT_ENABLED=false`,
  `STEPT_EMBEDDING_DIM=128`,
].join(' ')

export default defineConfig({
  testDir: './tests',
  timeout: 45_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['github'], ['html', { open: 'never' }]] : [['list']],
  use: {
    baseURL: `http://localhost:${FRONTEND_PORT}`,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      // The backend serves the embeddable widget from `widget/dist` (gitignored),
      // and the DAP journeys drive that real bundle on a host page — so build it
      // here instead of relying on a prior `make build`. It takes ~1s.
      command: `bash -c "cd .. && pnpm --filter @stept/widget build && cd backend && rm -f e2e.db && ${backendEnv} uv run python -m app.seed && ${backendEnv} uv run uvicorn app.main:app --port ${BACKEND_PORT}"`,
      url: `http://localhost:${BACKEND_PORT}/api/v1/healthz`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: `bash -c "cd ../frontend && STEPT_BACKEND_ORIGIN=http://localhost:${BACKEND_PORT} pnpm vite --port ${FRONTEND_PORT} --strictPort"`,
      url: `http://localhost:${FRONTEND_PORT}`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
})
