import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'

import { useAuthStore } from '@/stores/auth'
import { mockFetch, renderApp } from '@/test/helpers'
import { WorkingHoursPanel } from '@/features/settings/components/WorkingHoursPanel'

const ISO = '2026-01-01T00:00:00Z'

function setupAuth(permissions = ['conversations:read', 'channels:manage']) {
  useAuthStore.setState({
    accessToken: 't',
    user: { id: 'u1', email: 'me@stept.co', name: 'Me' },
    workspaceId: 'ws1',
    bootstrapped: true,
    memberships: [
      {
        id: 'me',
        role: 'admin',
        is_available: true,
        permissions,
        workspace: { id: 'ws1', name: 'WS', slug: 'ws', settings: {} },
      },
    ],
  })
}

const INBOX = {
  id: 'i1',
  name: 'Website',
  channel_type: 'widget',
  enabled: true,
  config: {},
  widget_key: 'wk',
  has_secrets: false,
  embed_snippet: '',
  created_at: ISO,
  updated_at: ISO,
}

function hours(overrides: Record<string, unknown> = {}) {
  return {
    enabled: true,
    timezone: 'Europe/Berlin',
    out_of_office_message: 'Back at 9',
    currently_open: false,
    days: [
      { day_of_week: 0, closed_all_day: false, open_all_day: false, open_minute: 540, close_minute: 1020 },
      { day_of_week: 5, closed_all_day: true, open_all_day: false, open_minute: 540, close_minute: 1020 },
    ],
    ...overrides,
  }
}

afterEach(() => {
  cleanup()
  useAuthStore.setState({ accessToken: null, memberships: [], workspaceId: null, user: null })
})

describe('WorkingHoursPanel', () => {
  it('renders the saved schedule and the open/closed badge', async () => {
    setupAuth()
    mockFetch({
      'GET /api/v1/w/ws1/inboxes': () => ({ body: [INBOX] }),
      'GET /api/v1/w/ws1/inboxes/i1/working-hours': () => ({ body: hours() }),
    })
    renderApp(<WorkingHoursPanel />)

    expect(await screen.findByText('Closed now')).toBeInTheDocument()
    expect(await screen.findByDisplayValue('Europe/Berlin')).toBeInTheDocument()
    expect(await screen.findByDisplayValue('Back at 9')).toBeInTheDocument()
    // Monday is open 09:00–17:00, Saturday is closed.
    expect(screen.getByLabelText('Monday open')).toBeChecked()
    expect(screen.getByLabelText('Saturday open')).not.toBeChecked()
  })

  it('replaces the whole week on save', async () => {
    setupAuth()
    let body: { days?: unknown[]; enabled?: boolean } | null = null
    mockFetch({
      'GET /api/v1/w/ws1/inboxes': () => ({ body: [INBOX] }),
      'GET /api/v1/w/ws1/inboxes/i1/working-hours': () => ({ body: hours() }),
      'PUT /api/v1/w/ws1/inboxes/i1/working-hours': (init) => {
        body = JSON.parse(String(init?.body))
        return { body: hours() }
      },
    })
    renderApp(<WorkingHoursPanel />)

    await userEvent.click(await screen.findByRole('button', { name: /save working hours/i }))
    await waitFor(() => expect(body).not.toBeNull())
    expect(body!.days).toHaveLength(7)
    expect(body!.enabled).toBe(true)
  })

  it('blocks saving when a day closes before it opens', async () => {
    setupAuth()
    mockFetch({
      'GET /api/v1/w/ws1/inboxes': () => ({ body: [INBOX] }),
      'GET /api/v1/w/ws1/inboxes/i1/working-hours': () => ({ body: hours() }),
    })
    renderApp(<WorkingHoursPanel />)

    const opening = await screen.findByLabelText('Monday opening time')
    await userEvent.clear(opening)
    await userEvent.type(opening, '20:00')

    expect(
      await screen.findByText(/closing time after its opening time/i)
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /save working hours/i })).toBeDisabled()
  })

  it('hides the save button without channels:manage', async () => {
    setupAuth(['conversations:read'])
    mockFetch({
      'GET /api/v1/w/ws1/inboxes': () => ({ body: [INBOX] }),
      'GET /api/v1/w/ws1/inboxes/i1/working-hours': () => ({ body: hours() }),
    })
    renderApp(<WorkingHoursPanel />)

    await screen.findByText('Working hours')
    expect(screen.queryByRole('button', { name: /save working hours/i })).toBeNull()
  })

  it('explains itself when there are no inboxes yet', async () => {
    setupAuth()
    mockFetch({ 'GET /api/v1/w/ws1/inboxes': () => ({ body: [] }) })
    renderApp(<WorkingHoursPanel />)
    expect(await screen.findByText(/create an inbox first/i)).toBeInTheDocument()
  })
})
