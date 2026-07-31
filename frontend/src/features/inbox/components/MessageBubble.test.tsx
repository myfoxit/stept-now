import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { renderApp } from '@/test/helpers'
import { MessageBubble } from '@/features/inbox/components/MessageBubble'
import type { Message } from '@/features/inbox/api'

function makeMessage(overrides: Partial<Message>): Message {
  return {
    id: 'm1',
    conversation_id: 'c1',
    direction: 'in',
    visibility: 'public',
    author_type: 'contact',
    author_id: null,
    author_name: 'Ada Lovelace',
    content: 'Hello world',
    attachments: [],
    source_id: null,
    delivery_status: null,
    delivery_error: null,
    meta: {},
    created_at: new Date().toISOString(),
    ...overrides,
  }
}

describe('MessageBubble', () => {
  it('renders an inbound public message', () => {
    const { container } = renderApp(<MessageBubble message={makeMessage({})} />)
    expect(screen.getByText('Hello world')).toBeInTheDocument()
    const bubble = container.querySelector('[data-message-variant="public"]')
    expect(bubble).toHaveAttribute('data-direction', 'in')
  })

  it('renders a private note distinctly with a lock affordance', () => {
    const { container } = renderApp(
      <MessageBubble message={makeMessage({ visibility: 'note', content: 'internal only' })} />
    )
    expect(container.querySelector('[data-message-variant="note"]')).toBeTruthy()
    expect(screen.getByText(/internal note/i)).toBeInTheDocument()
    expect(screen.getByText('internal only')).toBeInTheDocument()
  })

  it('renders an activity line centered', () => {
    const { container } = renderApp(
      <MessageBubble message={makeMessage({ visibility: 'activity', content: 'Sage joined' })} />
    )
    expect(container.querySelector('[data-message-variant="activity"]')).toBeTruthy()
    expect(screen.getByText('Sage joined')).toBeInTheDocument()
  })

  it('renders citation chips from meta.citations on agent replies', () => {
    renderApp(
      <MessageBubble
        message={makeMessage({
          direction: 'out',
          author_type: 'agent',
          author_name: 'Sage',
          content: 'See the guide [1]',
          meta: { citations: [{ n: 1, title: 'Widget guide', url: 'https://docs.example/widget' }] },
        })}
      />
    )
    expect(screen.getByTestId('citations')).toBeInTheDocument()
    const link = screen.getByRole('link', { name: /widget guide/i })
    expect(link).toHaveAttribute('href', 'https://docs.example/widget')
  })

  it('shows delivery status on outbound messages', () => {
    renderApp(
      <MessageBubble
        message={makeMessage({ direction: 'out', author_type: 'user', delivery_status: 'pending' })}
      />
    )
    expect(screen.getByText(/sending/i)).toBeInTheDocument()
  })
})
