import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { renderApp } from '@/test/helpers'
import { useAuthStore } from '@/stores/auth'
import { ConversationListPane } from '@/features/inbox/components/ConversationListPane'

function jsonResponse(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

function makeRow(id: string, name: string, status: string) {
  return {
    id,
    number: 1,
    subject: null,
    status,
    priority: 'none',
    contact: { id: `ct-${id}`, name, email: null, avatar_url: null },
    inbox: { id: 'ib1', name: 'Web', channel_type: 'widget' },
    assignee: null,
    last_message_preview: `preview ${name}`,
    last_activity_at: new Date().toISOString(),
    unread: false,
    tag_ids: [],
    waiting_since: null,
  }
}

class FakeWebSocket {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSING = 2
  static CLOSED = 3
  readyState = 0
  onopen: (() => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  onmessage: (() => void) | null = null
  send() {}
  close() {}
}

beforeEach(() => {
  useAuthStore.setState({
    accessToken: 't',
    user: { id: 'u1', email: 'r@stept.co', name: 'Reggie' },
    memberships: [
      {
        id: 'm1',
        role: 'admin',
        is_available: true,
        workspace: { id: 'ws1', name: 'WS', slug: 'ws', settings: {} },
        permissions: ['conversations:read', 'conversations:write'],
      },
    ],
    workspaceId: 'ws1',
    bootstrapped: true,
  })
  vi.stubGlobal('WebSocket', FakeWebSocket)
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const raw = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
      const url = new URL(raw, 'http://localhost')
      const path = url.pathname
      if (path.endsWith('/conversations/counts')) {
        return jsonResponse({ open: 2, unassigned: 1, mine: 0, pending: 1, snoozed: 0, resolved: 3 })
      }
      if (path.endsWith('/conversations')) {
        const status = url.searchParams.get('status')
        const items =
          status === 'resolved' ? [makeRow('c2', 'Zed Resolved', 'resolved')] : [makeRow('c1', 'Ada Open', 'open')]
        return jsonResponse({ items, next_cursor: null })
      }
      if (path.endsWith('/inboxes')) return jsonResponse([])
      if (path.endsWith('/tags')) return jsonResponse([])
      throw new Error(`unmocked ${path}`)
    })
  )
})

afterEach(() => vi.unstubAllGlobals())

describe('ConversationListPane', () => {
  it('renders conversations and live status counts', async () => {
    renderApp(<ConversationListPane onSelect={() => {}} />)
    expect(await screen.findByText('Ada Open')).toBeInTheDocument()
    // "Open" tab badge = 2
    expect(screen.getByRole('button', { name: /open 2/i })).toBeInTheDocument()
  })

  it('switches the filter when a status tab is clicked', async () => {
    renderApp(<ConversationListPane onSelect={() => {}} />)
    expect(await screen.findByText('Ada Open')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /resolved 3/i }))
    expect(await screen.findByText('Zed Resolved')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByText('Ada Open')).not.toBeInTheDocument())
  })
})
