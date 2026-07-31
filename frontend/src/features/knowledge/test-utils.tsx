import type { ReactElement } from 'react'

import { SidebarProvider } from '@/components/ui/sidebar'
import { renderApp } from '@/test/helpers'
import { useAuthStore } from '@/stores/auth'

/** Seed the auth store with a workspace + permissions for feature tests. */
export function seedAuth(
  permissions: string[] = ['knowledge:read', 'knowledge:write'],
  { token = null as string | null } = {}
) {
  useAuthStore.setState({
    accessToken: token,
    user: { id: 'u1', email: 'a@b.co', name: 'Ada' },
    workspaceId: 'w1',
    memberships: [
      {
        id: 'm1',
        role: 'admin',
        is_available: true,
        workspace: { id: 'w1', name: 'Acme', slug: 'acme', settings: {} },
        permissions,
      },
    ],
    bootstrapped: true,
  })
}

/** Render a full page inside the sidebar context the AppShell normally provides. */
export function renderPage(ui: ReactElement, opts?: { route?: string }) {
  return renderApp(<SidebarProvider>{ui}</SidebarProvider>, opts)
}

const NOW = '2026-07-31T10:00:00Z'

export function makeSource(overrides: Record<string, unknown> = {}) {
  return {
    id: 'src1',
    type: 'files',
    name: 'Product docs',
    config: {},
    status: 'idle',
    error: null,
    last_synced_at: null,
    document_count: 3,
    has_secrets: false,
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  }
}

export function makeAnalyticsOverview(overrides: Record<string, unknown> = {}) {
  return {
    queries: {
      total: 1284,
      per_day: [
        { date: '2026-07-29', count: 40 },
        { date: '2026-07-30', count: 55 },
        { date: '2026-07-31', count: 32 },
      ],
      zero_result_count: 24,
      zero_result_rate: 0.12,
      avg_top_score: 0.62,
      avg_latency_ms: 45,
      by_source: [
        { source: 'Product docs', count: 90 },
        { source: 'Help center', count: 37 },
      ],
    },
    top_queries: [
      { query: 'install widget', count: 31, avg_top_score: 0.71 },
      { query: 'reset password', count: 18, avg_top_score: 0.64 },
    ],
    zero_result_queries: [
      { query: 'sso setup', count: 9 },
      { query: 'refund policy', count: 4 },
    ],
    feedback: { up: 42, down: 6, negative_rate: 0.125 },
    ai: { runs: 200, completed: 150, handed_off: 50, deflection_rate: 0.75 },
    ...overrides,
  }
}

export function makeDocument(overrides: Record<string, unknown> = {}) {
  return {
    id: 'doc1',
    source_id: 'src1',
    title: 'Getting started',
    uri: null,
    mime: 'text/markdown',
    content_hash: 'abc',
    status: 'indexed',
    error: null,
    token_count: 1200,
    meta: {},
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  }
}
