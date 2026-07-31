import { useAuthStore } from '@/stores/auth'

const NOW = '2026-07-31T10:00:00Z'

/** Seed the auth store with a workspace + permissions for checklist tests. */
export function seedAuth(permissions: string[] = ['tours:read', 'tours:manage']) {
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

export function makeItem(overrides: Record<string, unknown> = {}) {
  return {
    id: 'i1',
    title: 'Take the welcome tour',
    body: 'Two minutes, tops.',
    action: { type: 'none' },
    completion: { type: 'manual' },
    ...overrides,
  }
}

export function makeChecklist(overrides: Record<string, unknown> = {}) {
  return {
    id: 'cl1',
    name: 'Getting started',
    description: 'First steps in Stept',
    status: 'draft',
    items: [makeItem()],
    trigger: { type: 'url_match', url_pattern: '*' },
    audience: { type: 'all', filters: [] },
    theme: { accent: '#6366f1', position: 'bottom-right' },
    launcher: { label: 'Getting started', auto_open_once: true },
    priority: 0,
    version: 1,
    created_by: 'u1',
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  }
}

export function makeStats(overrides: Record<string, unknown> = {}) {
  return {
    views: null,
    starts: 12,
    completions: 3,
    completion_rate: 0.25,
    items: [{ id: 'i1', title: 'Take the welcome tour', completed_count: 7 }],
    ...overrides,
  }
}

export const tourOptions = [
  { id: 't1', name: 'Welcome tour', status: 'live' },
  { id: 't2', name: 'Automation basics', status: 'draft' },
]
