import { useAuthStore } from '@/stores/auth'

import type { Tour, TourEvent, TourEventsPage, TourStats, TourStep } from './api'

const NOW = '2026-07-31T10:00:00Z'

/** Seed the auth store with a workspace + permissions for tour tests. */
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

export function makeStep(overrides: Partial<TourStep> = {}): TourStep {
  return {
    id: 's1',
    type: 'tooltip',
    selector: '#signup',
    fallback_selectors: [],
    text_hint: '',
    target: null,
    title: 'Start here',
    body: 'Click **sign up**.',
    media: null,
    screenshot_key: null,
    placement: 'auto',
    advance: { on: 'button' },
    action: null,
    wait: null,
    ...overrides,
  }
}

export function makeTour(overrides: Partial<Tour> = {}): Tour {
  return {
    id: 't1',
    name: 'Welcome tour',
    description: 'First run walkthrough',
    kind: 'flow',
    status: 'draft',
    trigger: { type: 'url_match', url_pattern: '/app*' },
    audience: { type: 'all', filters: [] },
    schedule: {},
    frequency: { type: 'until_dismissed' },
    priority: 0,
    settings: { mode: 'guided', backdrop: true, show_progress: true, dismissable: true },
    steps: [makeStep()],
    theme: { accent: '#6366f1' },
    version: 3,
    created_by: 'u1',
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  }
}

export function makeStats(overrides: Partial<TourStats> = {}): TourStats {
  return {
    starts: 120,
    completions: 42,
    dismissals: 18,
    completion_rate: 0.35,
    unique_starts: 96,
    step_errors: 4,
    step_blocked: 6,
    by_day: [
      { date: '2026-07-29', starts: 40, completions: 12 },
      { date: '2026-07-30', starts: 45, completions: 18 },
      { date: '2026-07-31', starts: 35, completions: 12 },
    ],
    steps: [
      { index: 0, title: 'Start here', viewed: 120, drop_off: 30, healed: 0 },
      { index: 1, title: 'Invite a teammate', viewed: 90, drop_off: 48, healed: 7 },
    ],
    ...overrides,
  }
}

export function makeEvent(overrides: Partial<TourEvent> = {}): TourEvent {
  return {
    id: 'e1',
    event: 'step_viewed',
    step_index: 1,
    contact_id: 'c1234567890',
    meta: { url: 'https://acme.test/app' },
    created_at: NOW,
    ...overrides,
  }
}

export function makeEventsPage(overrides: Partial<TourEventsPage> = {}): TourEventsPage {
  return {
    items: [makeEvent()],
    total: 1,
    limit: 25,
    offset: 0,
    ...overrides,
  }
}
