import { QueryClient } from '@tanstack/react-query'
import { describe, expect, it } from 'vitest'

import {
  applyConversationUpdated,
  applyMessageCreated,
  flattenMessages,
} from '@/features/inbox/hooks'
import type { Conversation, ConversationListItem, Message } from '@/features/inbox/api'

const WS = 'ws1'

function msg(overrides: Partial<Message>): Message {
  return {
    id: 'm-new',
    conversation_id: 'c1',
    direction: 'in',
    visibility: 'public',
    author_type: 'contact',
    author_id: null,
    author_name: 'Grace',
    content: 'a new inbound message',
    attachments: [],
    source_id: null,
    delivery_status: null,
    delivery_error: null,
    meta: {},
    created_at: new Date().toISOString(),
    ...overrides,
  }
}

function row(overrides: Partial<ConversationListItem>): ConversationListItem {
  return {
    id: 'c1',
    number: 1,
    subject: null,
    status: 'open',
    priority: 'none',
    contact: { id: 'ct1', name: 'Grace', email: null, avatar_url: null },
    inbox: { id: 'ib1', name: 'Web', channel_type: 'widget' },
    assignee: null,
    last_message_preview: 'old preview',
    last_activity_at: new Date(Date.now() - 10000).toISOString(),
    unread: false,
    tag_ids: [],
    waiting_since: null,
    ...overrides,
  }
}

function conversation(overrides: Partial<Conversation>): Conversation {
  return {
    id: 'c1',
    number: 1,
    subject: null,
    status: 'open',
    priority: 'none',
    snoozed_until: null,
    contact: {
      id: 'ct1',
      name: 'Grace',
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

describe('applyMessageCreated', () => {
  it('appends a realtime message to the open thread and dedupes by id', () => {
    const qc = new QueryClient()
    qc.setQueryData(['inbox', WS, 'messages', 'c1'], {
      pages: [{ items: [msg({ id: 'm-old', content: 'first' })], next_cursor: null }],
      pageParams: [undefined],
    })

    const incoming = msg({ id: 'm-new', content: 'second' })
    applyMessageCreated(qc, WS, 'c1', { message: incoming, conversation: {} })
    applyMessageCreated(qc, WS, 'c1', { message: incoming, conversation: {} }) // duplicate

    const flat = flattenMessages(qc.getQueryData(['inbox', WS, 'messages', 'c1']))
    expect(flat.map((m) => m.content)).toEqual(['first', 'second'])
  })

  it('bumps the matching list row to the front and marks it unread', () => {
    const qc = new QueryClient()
    qc.setQueryData(['inbox', WS, 'conversations', { status: ['open'] }], {
      pages: [{ items: [row({ id: 'other', number: 2 }), row({ id: 'c1' })], next_cursor: null }],
      pageParams: [undefined],
    })

    applyMessageCreated(qc, WS, 'c2', {
      message: msg({ conversation_id: 'c1', content: 'ping!' }),
      conversation: { status: 'open', last_activity_at: new Date().toISOString() },
    })

    const data = qc.getQueryData<{ pages: { items: ConversationListItem[] }[] }>([
      'inbox',
      WS,
      'conversations',
      { status: ['open'] },
    ])
    const items = data!.pages[0].items
    expect(items[0].id).toBe('c1')
    expect(items[0].unread).toBe(true)
    expect(items[0].last_message_preview).toBe('ping!')
  })
})

describe('applyConversationUpdated', () => {
  it('updates the detail cache and patches the matching list row', () => {
    const qc = new QueryClient()
    qc.setQueryData(['inbox', WS, 'conversation', 'c1'], conversation({}))
    qc.setQueryData(['inbox', WS, 'conversations', { status: ['open'] }], {
      pages: [{ items: [row({ id: 'c1', status: 'open' })], next_cursor: null }],
      pageParams: [undefined],
    })

    applyConversationUpdated(qc, WS, conversation({ status: 'resolved', priority: 'high' }))

    const detail = qc.getQueryData<Conversation>(['inbox', WS, 'conversation', 'c1'])
    expect(detail?.status).toBe('resolved')

    const data = qc.getQueryData<{ pages: { items: ConversationListItem[] }[] }>([
      'inbox',
      WS,
      'conversations',
      { status: ['open'] },
    ])
    expect(data!.pages[0].items[0].status).toBe('resolved')
    expect(data!.pages[0].items[0].priority).toBe('high')
  })
})
