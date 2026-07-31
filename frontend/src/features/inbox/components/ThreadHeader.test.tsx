import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { mockFetch, renderApp } from '@/test/helpers'
import { useAuthStore } from '@/stores/auth'
import { ThreadHeader } from '@/features/inbox/components/ThreadHeader'
import type { Conversation } from '@/features/inbox/api'

function setupAuth(permissions: string[]) {
  useAuthStore.setState({
    accessToken: 't',
    user: { id: 'u1', email: 'r@stept.co', name: 'Reggie' },
    memberships: [
      {
        id: 'm1',
        role: 'admin',
        is_available: true,
        workspace: { id: 'ws1', name: 'WS', slug: 'ws', settings: {} },
        permissions,
      },
    ],
    workspaceId: 'ws1',
    bootstrapped: true,
  })
}

function conversation(overrides: Partial<Conversation> = {}): Conversation {
  return {
    id: 'c1',
    number: 42,
    subject: 'Billing question',
    status: 'open',
    priority: 'none',
    snoozed_until: null,
    contact: {
      id: 'ct1',
      name: 'Grace Hopper',
      email: null,
      avatar_url: null,
      external_id: null,
      phone: null,
      verified: false,
      attributes: {},
      last_seen_at: null,
      created_at: new Date().toISOString(),
    },
    inbox: { id: 'ib1', name: 'Web', channel_type: 'widget' },
    assignee: null,
    team_id: null,
    ai_agent_id: null,
    attributes: {},
    waiting_since: null,
    first_reply_at: null,
    resolved_at: null,
    last_activity_at: new Date().toISOString(),
    agent_last_seen_at: null,
    contact_last_seen_at: null,
    csat_requested: false,
    tag_ids: [],
    unread_count: 0,
    created_at: new Date().toISOString(),
    ...overrides,
  }
}

describe('ThreadHeader', () => {
  it('resolves a conversation via PATCH', async () => {
    setupAuth(['conversations:read', 'conversations:manage'])
    let patchBody: { status?: string } | null = null
    mockFetch({
      'GET /api/v1/w/ws1/members': () => ({ body: [] }),
      'GET /api/v1/w/ws1/teams': () => ({ body: [] }),
      'PATCH /api/v1/w/ws1/conversations/c1': (init) => {
        patchBody = JSON.parse(init!.body as string)
        return { body: conversation({ status: 'resolved' }) }
      },
    })
    renderApp(<ThreadHeader conversation={conversation()} />)
    await userEvent.click(screen.getByRole('button', { name: /resolve/i }))
    await waitFor(() => expect(patchBody).not.toBeNull())
    expect(patchBody!).toMatchObject({ status: 'resolved' })
  })

  it('disables workflow controls without conversations:manage', () => {
    setupAuth(['conversations:read'])
    mockFetch({
      'GET /api/v1/w/ws1/members': () => ({ body: [] }),
      'GET /api/v1/w/ws1/teams': () => ({ body: [] }),
    })
    renderApp(<ThreadHeader conversation={conversation()} />)
    expect(screen.getByRole('button', { name: /resolve/i })).toBeDisabled()
  })
})
