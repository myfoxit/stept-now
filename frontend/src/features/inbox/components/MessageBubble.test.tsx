import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'

import { useAuthStore } from '@/stores/auth'
import { mockFetch, renderApp } from '@/test/helpers'
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

describe('MessageBubble feedback thumbs', () => {
  function setupAuth(permissions: string[] = ['conversations:read', 'conversations:write']) {
    useAuthStore.setState({
      accessToken: 't',
      user: { id: 'u1', email: 'me@stept.co', name: 'Me' },
      workspaceId: 'ws1',
      bootstrapped: true,
      memberships: [
        {
          id: 'me',
          role: 'agent',
          is_available: true,
          permissions,
          workspace: { id: 'ws1', name: 'WS', slug: 'ws', settings: {} },
        },
      ],
    })
  }

  const agentReply = (): Message =>
    makeMessage({ direction: 'out', author_type: 'agent', author_name: 'Sage' })

  afterEach(() => {
    cleanup()
    useAuthStore.setState({ accessToken: null, memberships: [], workspaceId: null, user: null })
  })

  it('renders thumbs only for public outbound agent messages', async () => {
    setupAuth()
    mockFetch({ 'GET /api/v1/w/ws1/conversations/c1/messages/m1/feedback': () => ({ body: [] }) })

    const { unmount } = renderApp(<MessageBubble message={agentReply()} />)
    expect(await screen.findByRole('button', { name: /good response/i })).toBeInTheDocument()
    unmount()

    // Human agent reply → no thumbs.
    renderApp(<MessageBubble message={makeMessage({ direction: 'out', author_type: 'user' })} />)
    expect(screen.queryByRole('button', { name: /good response/i })).not.toBeInTheDocument()
    cleanup()

    // AI private note → no thumbs.
    renderApp(
      <MessageBubble
        message={makeMessage({ direction: 'out', author_type: 'agent', visibility: 'note' })}
      />
    )
    expect(screen.queryByRole('button', { name: /good response/i })).not.toBeInTheDocument()
  })

  it('POSTs the rating and toggles the highlighted thumb', async () => {
    setupAuth()
    const bodies: unknown[] = []
    mockFetch({
      'GET /api/v1/w/ws1/conversations/c1/messages/m1/feedback': () => ({ body: [] }),
      'POST /api/v1/w/ws1/conversations/c1/messages/m1/feedback': (init) => {
        bodies.push(JSON.parse(init!.body as string))
        return {
          status: 201,
          body: {
            id: 'f1',
            message_id: 'm1',
            conversation_id: 'c1',
            rating: 'up',
            comment: null,
            actor_type: 'user',
            actor_id: 'u1',
            created_at: new Date().toISOString(),
          },
        }
      },
    })

    renderApp(<MessageBubble message={agentReply()} />)
    const up = await screen.findByRole('button', { name: /good response/i })
    const down = screen.getByRole('button', { name: /bad response/i })

    await userEvent.click(up)
    await waitFor(() => expect(bodies).toHaveLength(1))
    expect(bodies[0]).toEqual({ rating: 'up' })
    expect(up).toHaveAttribute('aria-pressed', 'true')
    expect(down).toHaveAttribute('aria-pressed', 'false')

    // Switching to the other thumb re-POSTs and moves the highlight.
    await userEvent.click(down)
    await waitFor(() => expect(bodies).toHaveLength(2))
    expect(bodies[1]).toEqual({ rating: 'down' })
    expect(down).toHaveAttribute('aria-pressed', 'true')
    expect(up).toHaveAttribute('aria-pressed', 'false')
  })

  it('seeds the highlighted thumb from stored feedback', async () => {
    setupAuth()
    mockFetch({
      'GET /api/v1/w/ws1/conversations/c1/messages/m1/feedback': () => ({
        body: [
          {
            id: 'f1',
            message_id: 'm1',
            conversation_id: 'c1',
            rating: 'down',
            comment: null,
            actor_type: 'user',
            actor_id: 'u1',
            created_at: new Date().toISOString(),
          },
        ],
      }),
    })

    renderApp(<MessageBubble message={agentReply()} />)
    const down = await screen.findByRole('button', { name: /bad response/i })
    await waitFor(() => expect(down).toHaveAttribute('aria-pressed', 'true'))
  })
})
