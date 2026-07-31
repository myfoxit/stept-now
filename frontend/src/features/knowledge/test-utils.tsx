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
    created_at: NOW,
    updated_at: NOW,
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
