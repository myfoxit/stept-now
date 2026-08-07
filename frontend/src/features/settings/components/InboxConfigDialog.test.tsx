import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'

import { useAuthStore } from '@/stores/auth'
import { mockFetch, renderApp } from '@/test/helpers'

import { InboxConfigDialog } from './InboxConfigDialog'

function setupAuth() {
  useAuthStore.setState({
    accessToken: 't',
    user: { id: 'u1', email: 'me@stept.co', name: 'Me' },
    workspaceId: 'w1',
    bootstrapped: true,
    memberships: [
      {
        id: 'me',
        role: 'owner',
        is_available: true,
        permissions: ['channels:manage'],
        workspace: { id: 'w1', name: 'WS', slug: 'ws', settings: {} },
      },
    ],
  })
}

import type { Inbox } from '../api'

const widgetInbox = {
  id: 'ib1',
  name: 'Website widget',
  channel_type: 'widget',
  enabled: true,
  widget_key: 'wk_test',
  has_secrets: false,
  config: { greeting: 'Hi! How can we help?', accent_color: '#6366f1', auto_assign: false },
}

const agents = [
  { id: 'ag-live', name: 'Support bot', status: 'live', avatar_emoji: '🤖' },
  { id: 'ag-draft', name: 'Draft bot', status: 'draft', avatar_emoji: null },
]

afterEach(() => {
  cleanup()
  useAuthStore.setState({ accessToken: null, memberships: [], workspaceId: null, user: null })
})

describe('InboxConfigDialog — widget channel', () => {
  it('attaches a live AI agent to the inbox', async () => {
    setupAuth()
    let patchBody: Record<string, unknown> | undefined
    mockFetch({
      'GET /api/v1/w/w1/ai/agents': () => ({ body: agents }),
      'PATCH /api/v1/w/w1/inboxes/ib1': (init) => {
        patchBody = JSON.parse(init!.body as string)
        return { body: { ...widgetInbox, config: patchBody!.config } }
      },
    })

    renderApp(<InboxConfigDialog inbox={widgetInbox as unknown as Inbox} open onOpenChange={() => {}} />)

    // Draft agents cannot pick up traffic, so they must not be offered.
    await userEvent.click(await screen.findByLabelText('AI agent'))
    expect(await screen.findByText('🤖 Support bot')).toBeInTheDocument()
    expect(screen.queryByText('Draft bot')).not.toBeInTheDocument()

    await userEvent.click(screen.getByText('🤖 Support bot'))
    await userEvent.click(screen.getByRole('button', { name: /save configuration/i }))

    // This key is what engine._on_conversation_created keys off; without it the
    // AI never joins a real conversation.
    await waitFor(() => expect(patchBody).toBeDefined())
    expect((patchBody!.config as Record<string, unknown>).ai_agent_id).toBe('ag-live')
  })

  it('persists auto-assign as a boolean, including when switched off', async () => {
    setupAuth()
    let patchBody: Record<string, unknown> | undefined
    mockFetch({
      'GET /api/v1/w/w1/ai/agents': () => ({ body: [] }),
      'PATCH /api/v1/w/w1/inboxes/ib1': (init) => {
        patchBody = JSON.parse(init!.body as string)
        return { body: { ...widgetInbox, config: patchBody!.config } }
      },
    })

    const on = { ...widgetInbox, config: { ...widgetInbox.config, auto_assign: true } }
    renderApp(<InboxConfigDialog inbox={on as unknown as Inbox} open onOpenChange={() => {}} />)

    await userEvent.click(await screen.findByLabelText('Auto-assign new conversations'))
    await userEvent.click(screen.getByRole('button', { name: /save configuration/i }))

    await waitFor(() => expect(patchBody).toBeDefined())
    // `false` must be written, not omitted — an absent key reads as "unset".
    expect((patchBody!.config as Record<string, unknown>).auto_assign).toBe(false)
  })
})
