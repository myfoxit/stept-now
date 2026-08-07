import type { ReactElement } from 'react'

import { SidebarProvider } from '@/components/ui/sidebar'
import { renderApp } from '@/test/helpers'
import { useAuthStore } from '@/stores/auth'

/** Seed the auth store with a workspace + permissions for AI feature tests. */
export function seedAuth(
  permissions: string[] = ['ai:read', 'ai:manage', 'ai:approve', 'knowledge:read'],
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

export function makeStep(overrides: Record<string, unknown> = {}) {
  return {
    id: 's1',
    ord: 0,
    kind: 'llm_call',
    name: null,
    input: {},
    output: {},
    latency_ms: 12,
    input_tokens: 10,
    output_tokens: 5,
    created_at: NOW,
    ...overrides,
  }
}

export function makeApproval(overrides: Record<string, unknown> = {}) {
  return {
    id: 'ap1',
    run_id: 'r1',
    conversation_id: 'c1',
    agent_id: 'a1',
    agent_name: 'Sage',
    tool_key: 'close_conversation',
    tool_input: { closing_message: 'All sorted!' },
    status: 'pending',
    requested_at: NOW,
    expires_at: '2026-08-01T10:00:00Z',
    decided_by: null,
    decided_at: null,
    note: null,
    created_at: NOW,
    ...overrides,
  }
}

export function makeMcpApproval(overrides: Record<string, unknown> = {}) {
  return {
    id: 'm1',
    agent_id: 'a1',
    agent_name: 'Sage',
    tool_key: 'action_create_ticket',
    tool_input: { title: 'Bug report' },
    status: 'pending',
    requested_at: NOW,
    expires_at: '2026-08-01T10:00:00Z',
    ...overrides,
  }
}
