import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { renderApp } from '@/test/helpers'
import { ConversationRow } from '@/features/inbox/components/ConversationRow'
import type { ConversationListItem, Tag } from '@/features/inbox/api'

function makeItem(overrides: Partial<ConversationListItem> = {}): ConversationListItem {
  return {
    id: 'c1',
    number: 12,
    subject: 'Cannot log in',
    status: 'open',
    priority: 'none',
    contact: { id: 'ct1', name: 'Grace Hopper', email: 'grace@navy.mil', avatar_url: null },
    inbox: { id: 'ib1', name: 'Website', channel_type: 'widget' },
    assignee: null,
    last_message_preview: 'I keep getting an error',
    last_activity_at: new Date().toISOString(),
    unread: false,
    tag_ids: [],
    waiting_since: null,
    ...overrides,
  }
}

const tagsById = new Map<string, Tag>([
  ['t1', { id: 't1', name: 'vip', color: '#ff0000', created_at: new Date().toISOString() }],
])

describe('ConversationRow', () => {
  it('shows contact, preview and unread dot when unread', () => {
    renderApp(
      <ConversationRow item={makeItem({ unread: true })} active={false} tagsById={tagsById} onSelect={() => {}} />
    )
    expect(screen.getByText('Grace Hopper')).toBeInTheDocument()
    expect(screen.getByText('I keep getting an error')).toBeInTheDocument()
    expect(screen.getByLabelText('unread')).toBeInTheDocument()
  })

  it('shows a priority flag and AI badge for pending conversations', () => {
    renderApp(
      <ConversationRow
        item={makeItem({ status: 'pending', priority: 'high' })}
        active={false}
        tagsById={tagsById}
        onSelect={() => {}}
      />
    )
    expect(screen.getByText('AI')).toBeInTheDocument()
    expect(screen.getByTitle('Priority: high')).toBeInTheDocument()
  })

  it('renders tag dots from the tag lookup', () => {
    renderApp(
      <ConversationRow item={makeItem({ tag_ids: ['t1'] })} active={false} tagsById={tagsById} onSelect={() => {}} />
    )
    expect(screen.getByTitle('vip')).toBeInTheDocument()
  })

  it('calls onSelect with the conversation id when clicked', async () => {
    const onSelect = vi.fn()
    renderApp(<ConversationRow item={makeItem()} active={false} tagsById={tagsById} onSelect={onSelect} />)
    await userEvent.click(screen.getByRole('button'))
    expect(onSelect).toHaveBeenCalledWith('c1')
  })
})
