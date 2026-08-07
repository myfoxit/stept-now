import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'

import { mockFetch, renderApp } from '@/test/helpers'
import { useAuthStore } from '@/stores/auth'
import { Composer } from '@/features/inbox/components/Composer'

const canned = {
  id: 'cr1',
  shortcut: 'refund',
  content: 'Hi {{contact.name}}, here is our refund policy.',
  created_by: null,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
}

const messageOut = {
  id: 'm-sent',
  conversation_id: 'c1',
  direction: 'out',
  visibility: 'public',
  author_type: 'user',
  author_id: 'u1',
  author_name: 'Reggie',
  content: 'Hello',
  attachments: [],
  source_id: null,
  delivery_status: 'sent',
  delivery_error: null,
  meta: {},
  created_at: new Date().toISOString(),
}

function setupAuth() {
  useAuthStore.setState({
    accessToken: 't',
    user: { id: 'u1', email: 'r@stept.co', name: 'Reggie' },
    memberships: [],
    workspaceId: 'ws1',
    bootstrapped: true,
  })
}

describe('Composer', () => {
  beforeEach(setupAuth)

  it('toggles between reply and private note', async () => {
    mockFetch({ 'GET /api/v1/w/ws1/canned-responses': () => ({ body: [] }) })
    renderApp(<Composer conversationId="c1" contactName="Ada Lovelace" canWrite />)
    expect(screen.getByLabelText('Reply')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('tab', { name: /note/i }))
    expect(await screen.findByLabelText('Internal note')).toBeInTheDocument()
  })

  it('opens the canned-response picker on "/" and inserts substituted content', async () => {
    mockFetch({ 'GET /api/v1/w/ws1/canned-responses': () => ({ body: [canned] }) })
    renderApp(<Composer conversationId="c1" contactName="Ada Lovelace" canWrite />)
    const textarea = screen.getByLabelText('Reply')
    await userEvent.type(textarea, '/')
    expect(await screen.findByTestId('canned-picker')).toBeInTheDocument()
    await userEvent.click(screen.getByText('/refund'))
    expect((textarea as HTMLTextAreaElement).value).toContain('Hi Ada Lovelace, here is our refund policy.')
  })

  it('inserts a copilot suggestion with citation chips', async () => {
    mockFetch({
      'GET /api/v1/w/ws1/canned-responses': () => ({ body: [] }),
      'POST /api/v1/w/ws1/ai/copilot/suggest': () => ({
        body: { content: 'Drafted reply from AI', citations: [{ n: 1, title: 'Refund policy', url: null }] },
      }),
    })
    renderApp(<Composer conversationId="c1" contactName="Ada Lovelace" canWrite />)
    await userEvent.click(screen.getByRole('button', { name: /suggest reply/i }))
    await waitFor(() =>
      expect((screen.getByLabelText('Reply') as HTMLTextAreaElement).value).toBe('Drafted reply from AI')
    )
    expect(screen.getByText(/Refund policy/)).toBeInTheDocument()
  })

  it('sends on Enter', async () => {
    let sentBody: { content: string; visibility: string } | null = null
    mockFetch({
      'GET /api/v1/w/ws1/canned-responses': () => ({ body: [] }),
      'POST /api/v1/w/ws1/conversations/c1/messages': (init) => {
        sentBody = JSON.parse(init!.body as string)
        return { status: 201, body: messageOut }
      },
    })
    renderApp(<Composer conversationId="c1" contactName="Ada Lovelace" canWrite />)
    await userEvent.type(screen.getByLabelText('Reply'), 'Hello{Enter}')
    await waitFor(() => expect(sentBody).not.toBeNull())
    expect(sentBody!).toMatchObject({ content: 'Hello', visibility: 'public' })
  })

  it('gates the composer when the user cannot write', () => {
    mockFetch({})
    renderApp(<Composer conversationId="c1" contactName="Ada Lovelace" canWrite={false} />)
    expect(screen.getByText(/don't have permission/i)).toBeInTheDocument()
    expect(screen.queryByLabelText('Reply')).not.toBeInTheDocument()
  })
})
