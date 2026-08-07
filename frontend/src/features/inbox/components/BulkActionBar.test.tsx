import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'

import { useAuthStore } from '@/stores/auth'
import { mockFetch, renderApp } from '@/test/helpers'
import { BulkActionBar } from '@/features/inbox/components/BulkActionBar'
import type { Member, Tag, Team } from '@/features/inbox/api'

const ISO = '2026-01-01T00:00:00Z'

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
        permissions: ['conversations:read', 'conversations:manage'],
        workspace: { id: 'ws1', name: 'WS', slug: 'ws', settings: {} },
      },
    ],
  })
}

const MEMBERS: Member[] = [
  {
    id: 'm1',
    role: 'agent',
    is_available: true,
    workspace_id: 'ws1',
    created_at: ISO,
    user: { id: 'u2', email: 'ada@stept.co', name: 'Ada', created_at: ISO },
  },
]
const TEAMS: Team[] = [{ id: 't1', name: 'Billing', created_at: ISO }]
const TAGS: Tag[] = [{ id: 'g1', name: 'refund', color: '#f00', created_at: ISO }]

afterEach(() => {
  cleanup()
  useAuthStore.setState({ accessToken: null, memberships: [], workspaceId: null, user: null })
})

function render(onDone = () => {}) {
  return renderApp(
    <BulkActionBar
      selected={['c1', 'c2']}
      members={MEMBERS}
      teams={TEAMS}
      tags={TAGS}
      onDone={onDone}
    />
  )
}

describe('BulkActionBar', () => {
  it('shows how many rows are selected', () => {
    setupAuth()
    mockFetch({})
    render()
    expect(screen.getByText('2 selected')).toBeInTheDocument()
  })

  it('posts the selected ids with the chosen status', async () => {
    setupAuth()
    let body: unknown = null
    mockFetch({
      'POST /api/v1/w/ws1/conversations/bulk': (init) => {
        body = JSON.parse(String(init?.body))
        return { body: { requested: 2, succeeded: 2, failed: 0, errors: [] } }
      },
    })
    render()
    await userEvent.click(screen.getByLabelText('Set status'))
    await userEvent.click(await screen.findByRole('option', { name: /resolved/i }))
    await waitFor(() => expect(body).not.toBeNull())
    expect(body).toEqual({
      action: 'set_status',
      params: { status: 'resolved' },
      conversation_ids: ['c1', 'c2'],
    })
  })

  it('sends the "self" sentinel when assigning to me', async () => {
    setupAuth()
    let body: { params?: Record<string, unknown> } | null = null
    mockFetch({
      'POST /api/v1/w/ws1/conversations/bulk': (init) => {
        body = JSON.parse(String(init?.body))
        return { body: { requested: 2, succeeded: 2, failed: 0, errors: [] } }
      },
    })
    render()
    await userEvent.click(screen.getByLabelText('Assign to'))
    await userEvent.click(await screen.findByRole('option', { name: 'Me' }))
    await waitFor(() => expect(body).not.toBeNull())
    expect(body!.params).toEqual({ assignee_user_id: 'self' })
  })

  it('clears the selection when the batch succeeds', async () => {
    setupAuth()
    let cleared = false
    mockFetch({
      'POST /api/v1/w/ws1/conversations/bulk': () => ({
        body: { requested: 2, succeeded: 2, failed: 0, errors: [] },
      }),
    })
    render(() => {
      cleared = true
    })
    await userEvent.click(screen.getByLabelText('Set priority'))
    await userEvent.click(await screen.findByRole('option', { name: /urgent/i }))
    await waitFor(() => expect(cleared).toBe(true))
  })
})
