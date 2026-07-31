import { useAuthStore } from '@/stores/auth'

const NOW = '2026-07-31T10:00:00Z'

/** Seed the auth store with a workspace + permissions for campaign feature tests. */
export function seedAuth(permissions: string[] = ['automations:read', 'automations:manage']) {
  useAuthStore.setState({
    accessToken: 't',
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

export function resetAuth() {
  useAuthStore.setState({ accessToken: null, memberships: [], workspaceId: null, user: null })
}

export function makeCampaign(overrides: Record<string, unknown> = {}) {
  return {
    id: 'c1',
    title: 'Pricing nudge',
    message: 'Hi {{contact.name}}, check our plans',
    campaign_type: 'ongoing',
    status: 'draft',
    enabled: true,
    inbox_id: 'i1',
    sender_user_id: null,
    audience: {},
    trigger_rules: { url_pattern: '/pricing*', time_on_page_seconds: 30 },
    scheduled_at: null,
    sent_count: 0,
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  }
}

export function makeInbox(overrides: Record<string, unknown> = {}) {
  return {
    id: 'i1',
    name: 'Website widget',
    channel_type: 'widget',
    config: {},
    enabled: true,
    has_secrets: false,
    embed_snippet: null,
    widget_key: null,
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  }
}

export const widgetInbox = makeInbox()
export const emailInbox = makeInbox({ id: 'i2', name: 'Support email', channel_type: 'email' })

export const segment = { id: 's1', name: 'VIP customers', filters: [], created_by: null, created_at: NOW }
export const tag = { id: 't1', name: 'beta', color: '#ff0000', created_at: NOW }
export const member = {
  id: 'm2',
  workspace_id: 'w1',
  role: 'agent',
  custom_role_id: null,
  is_available: true,
  user: { id: 'u2', email: 'bob@acme.co', name: 'Bob' },
  created_at: NOW,
}
