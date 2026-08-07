import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'

import { useAuthStore } from '@/stores/auth'
import { useDrilldownStore } from '@/stores/drilldown'
import { mockFetch, renderApp } from '@/test/helpers'
import { BreakdownTable } from '@/features/reports/components/BreakdownTable'

function setupAuth() {
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
        permissions: ['reports:read', 'conversations:read'],
        workspace: { id: 'ws1', name: 'WS', slug: 'ws', settings: {} },
      },
    ],
  })
}

const AGENT_FILTER = {
  match: 'all',
  conditions: [{ field: 'assignee_user_id', op: 'eq', value: 'u2' }],
}

function breakdown() {
  return {
    dimension: 'agent',
    days: 7,
    rows: [
      {
        key: 'u2',
        label: 'Ada',
        new: 5,
        resolved: 3,
        resolution_rate: 0.6,
        median_first_response_minutes: 12,
        median_resolution_minutes: 90,
        filter: AGENT_FILTER,
      },
      {
        key: '',
        label: 'Unassigned',
        new: 2,
        resolved: 0,
        resolution_rate: 0,
        median_first_response_minutes: null,
        median_resolution_minutes: null,
        filter: {
          match: 'all',
          conditions: [{ field: 'assignee_user_id', op: 'not_exists', value: null }],
        },
      },
    ],
  }
}

afterEach(() => {
  cleanup()
  useDrilldownStore.setState({ pending: null })
  useAuthStore.setState({ accessToken: null, memberships: [], workspaceId: null, user: null })
})

describe('BreakdownTable', () => {
  it('renders rows with rates and medians', async () => {
    setupAuth()
    mockFetch({ 'GET /api/v1/w/ws1/reports/breakdown': () => ({ body: breakdown() }) })
    renderApp(<BreakdownTable days={7} />)

    expect(await screen.findByText('Ada')).toBeInTheDocument()
    expect(screen.getByText('60%')).toBeInTheDocument()
    expect(screen.getByText('12m')).toBeInTheDocument()
    expect(screen.getByText('1.5h')).toBeInTheDocument()
    // The unassigned bucket has no medians to show.
    expect(screen.getAllByText('—').length).toBeGreaterThan(0)
  })

  it('parks the row filter for the inbox when a number is clicked', async () => {
    setupAuth()
    mockFetch({ 'GET /api/v1/w/ws1/reports/breakdown': () => ({ body: breakdown() }) })
    renderApp(<BreakdownTable days={7} />)

    await userEvent.click(
      await screen.findByRole('button', { name: /show the 5 conversations for ada/i })
    )
    await waitFor(() => expect(useDrilldownStore.getState().pending).not.toBeNull())
    const pending = useDrilldownStore.getState().pending!
    expect(pending.query).toEqual(AGENT_FILTER)
    expect(pending.label).toContain('Ada')
  })

  it('handles an empty window', async () => {
    setupAuth()
    mockFetch({
      'GET /api/v1/w/ws1/reports/breakdown': () => ({
        body: { dimension: 'agent', days: 7, rows: [] },
      }),
    })
    renderApp(<BreakdownTable days={7} />)
    expect(await screen.findByText(/no conversations in this window/i)).toBeInTheDocument()
  })
})
