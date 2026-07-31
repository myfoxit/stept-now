import { useAuthStore } from '@/stores/auth'

const NOW = '2026-07-31T10:00:00Z'

/** Seed the auth store with a workspace + permissions for survey tests. */
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

export function makeQuestion(overrides: Record<string, unknown> = {}) {
  return {
    id: 'q1',
    type: 'nps',
    question: 'How likely are you to recommend Stept?',
    required: true,
    options: null,
    ...overrides,
  }
}

export function makeSurvey(overrides: Record<string, unknown> = {}) {
  return {
    id: 'sv1',
    name: 'How are we doing?',
    status: 'draft',
    questions: [makeQuestion()],
    presentation: 'slideout',
    trigger: { type: 'url_match', url_pattern: '*' },
    audience: { type: 'all', filters: [] },
    schedule: { start_at: null, end_at: null },
    frequency: { type: 'once', cooldown_hours: null },
    priority: 0,
    theme: { accent: '#6366f1' },
    thanks_message: 'Thanks for the feedback!',
    version: 1,
    created_by: 'u1',
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  }
}

export function makeResults(overrides: Record<string, unknown> = {}) {
  return {
    responses: 20,
    completed: 15,
    completion_rate: 0.75,
    by_day: [
      { date: '2026-07-29', responses: 4 },
      { date: '2026-07-30', responses: 9 },
      { date: '2026-07-31', responses: 7 },
    ],
    nps: { score: 40, promoters: 10, passives: 5, detractors: 5 },
    ratings: { avg: 4.2, distribution: { '1': 1, '2': 0, '3': 2, '4': 5, '5': 12 } },
    select: [
      {
        question_id: 'q3',
        question: 'What do you use Stept for?',
        counts: { Support: 9, Onboarding: 6, Both: 2 },
      },
    ],
    text_answers: [],
    ...overrides,
  }
}

export function makeResponsePage(overrides: Record<string, unknown> = {}) {
  return {
    items: [
      {
        id: 'r1',
        survey_id: 'sv1',
        contact_id: 'c1',
        answers: [
          { question_id: 'q1', value: 9 },
          { question_id: 'q2', value: 'The inbox is fast and the AI drafts are useful.' },
        ],
        completed: true,
        meta: { url: 'https://app.example.com/inbox' },
        created_at: NOW,
      },
      {
        id: 'r2',
        survey_id: 'sv1',
        contact_id: null,
        answers: [{ question_id: 'q2', value: 'More keyboard shortcuts please.' }],
        completed: false,
        meta: {},
        created_at: NOW,
      },
    ],
    total: 2,
    limit: 10,
    offset: 0,
    ...overrides,
  }
}
