import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'

import { useAuthStore } from '@/stores/auth'
import { mockFetch, renderApp } from '@/test/helpers'
import { WatchersCard } from '@/features/inbox/components/WatchersCard'

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
        permissions: ['conversations:read', 'conversations:write'],
        workspace: { id: 'ws1', name: 'WS', slug: 'ws', settings: {} },
      },
    ],
  })
}

const MEMBERS = [
  {
    id: 'm1',
    role: 'admin',
    is_available: true,
    workspace_id: 'ws1',
    created_at: ISO,
    user: { id: 'u1', email: 'me@stept.co', name: 'Me', created_at: ISO },
  },
  {
    id: 'm2',
    role: 'agent',
    is_available: true,
    workspace_id: 'ws1',
    created_at: ISO,
    user: { id: 'u2', email: 'ada@stept.co', name: 'Ada', created_at: ISO },
  },
]

function participant(userId: string, muted = false, reason = 'assignee') {
  return { id: `p-${userId}`, user_id: userId, reason, muted, created_at: ISO }
}

afterEach(() => {
  cleanup()
  useAuthStore.setState({ accessToken: null, memberships: [], workspaceId: null, user: null })
})

describe('WatchersCard', () => {
  it('lists active watchers with why they were added', async () => {
    setupAuth()
    mockFetch({
      'GET /api/v1/w/ws1/conversations/c1/participants': () => ({
        body: [participant('u2', false, 'mention')],
      }),
      'GET /api/v1/w/ws1/members': () => ({ body: MEMBERS }),
    })
    renderApp(<WatchersCard conversationId="c1" />)

    expect(await screen.findByText('Ada')).toBeInTheDocument()
    expect(screen.getByText('mention')).toBeInTheDocument()
  })

  it('hides muted participants', async () => {
    setupAuth()
    mockFetch({
      'GET /api/v1/w/ws1/conversations/c1/participants': () => ({
        body: [participant('u2', true)],
      }),
      'GET /api/v1/w/ws1/members': () => ({ body: MEMBERS }),
    })
    renderApp(<WatchersCard conversationId="c1" />)

    expect(await screen.findByText(/nobody is watching/i)).toBeInTheDocument()
  })

  it('offers Watch when I am not a participant', async () => {
    setupAuth()
    let posted = false
    mockFetch({
      'GET /api/v1/w/ws1/conversations/c1/participants': () => ({ body: [] }),
      'GET /api/v1/w/ws1/members': () => ({ body: MEMBERS }),
      'POST /api/v1/w/ws1/conversations/c1/participants': () => {
        posted = true
        return { body: [participant('u1')], status: 201 }
      },
    })
    renderApp(<WatchersCard conversationId="c1" />)

    await userEvent.click(await screen.findByRole('button', { name: /watch/i }))
    await waitFor(() => expect(posted).toBe(true))
  })

  it('offers Leave when I am already watching', async () => {
    setupAuth()
    let deleted = false
    mockFetch({
      'GET /api/v1/w/ws1/conversations/c1/participants': () => ({ body: [participant('u1')] }),
      'GET /api/v1/w/ws1/members': () => ({ body: MEMBERS }),
      'DELETE /api/v1/w/ws1/conversations/c1/participants/u1': () => {
        deleted = true
        return { body: { message: 'ok' } }
      },
    })
    renderApp(<WatchersCard conversationId="c1" />)

    await userEvent.click(await screen.findByRole('button', { name: /leave/i }))
    await waitFor(() => expect(deleted).toBe(true))
  })
})
