import { act, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { mockFetch } from '@/test/helpers'

import { Component as ApprovalsPage } from './ApprovalsPage'
import { makeApproval, makeMcpApproval, renderPage, seedAuth } from '../test-utils'

/* Minimal controllable WebSocket so we can inject realtime frames. */
const wsInstances: FakeWS[] = []
class FakeWS {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSING = 2
  static CLOSED = 3
  url: string
  readyState = 1
  onopen: ((ev?: unknown) => void) | null = null
  onmessage: ((ev: { data: string }) => void) | null = null
  onclose: ((ev?: unknown) => void) | null = null
  onerror: ((ev?: unknown) => void) | null = null
  constructor(url: string) {
    this.url = url
    wsInstances.push(this)
  }
  send() {}
  close() {
    this.readyState = 3
  }
}

describe('ApprovalsPage', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('approves a pending request and removes it from the queue', async () => {
    seedAuth()
    const fetchFn = mockFetch({
      'GET /api/v1/w/w1/ai/approvals': () => ({ body: [makeApproval({ id: 'ap1' })] }),
      'GET /api/v1/w/w1/mcp-approvals': () => ({ body: [] }),
      'POST /api/v1/w/w1/ai/approvals/ap1/decide': () => ({
        body: makeApproval({ id: 'ap1', status: 'approved' }),
      }),
    })
    renderPage(<ApprovalsPage />)

    expect(await screen.findByText('close_conversation')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /approve/i }))

    await waitFor(() => {
      expect(
        fetchFn.mock.calls.some(
          ([url, init]) =>
            init?.method === 'POST' && String(url).endsWith('/ai/approvals/ap1/decide')
        )
      ).toBe(true)
    })
    await waitFor(() => expect(screen.queryByText('close_conversation')).not.toBeInTheDocument())
  })

  it('prepends an approval that arrives over the realtime channel', async () => {
    vi.stubGlobal('WebSocket', FakeWS)
    seedAuth(['ai:read', 'ai:manage', 'ai:approve'], { token: 'tok' })
    mockFetch({
      'GET /api/v1/w/w1/ai/approvals': () => ({ body: [] }),
      'GET /api/v1/w/w1/mcp-approvals': () => ({ body: [] }),
    })
    renderPage(<ApprovalsPage />)

    expect(await screen.findByText(/all caught up/i)).toBeInTheDocument()

    const socket = wsInstances.at(-1)
    expect(socket).toBeDefined()
    act(() => {
      socket!.onopen?.()
      socket!.onmessage?.({
        data: JSON.stringify({
          type: 'approval.pending',
          data: {
            approval_id: 'ap9',
            tool_key: 'close_conversation',
            agent_name: 'Sage',
            conversation_id: 'c1',
            tool_input: { closing_message: 'Done' },
          },
        }),
      })
    })

    expect(await screen.findByText('close_conversation')).toBeInTheDocument()
    expect(screen.getByText('Sage')).toBeInTheDocument()
  })

  it('lists MCP approvals and records an approve decision', async () => {
    seedAuth()
    let pending = [makeMcpApproval({ id: 'm1' })]
    let decideBody: unknown
    mockFetch({
      'GET /api/v1/w/w1/ai/approvals': () => ({ body: [] }),
      'GET /api/v1/w/w1/mcp-approvals': () => ({ body: pending }),
      'POST /api/v1/w/w1/mcp-approvals/m1/decide': (init) => {
        decideBody = JSON.parse(init!.body as string)
        pending = []
        return { body: makeMcpApproval({ id: 'm1', status: 'approved' }) }
      },
    })
    renderPage(<ApprovalsPage />)

    expect(await screen.findByText('MCP clients')).toBeInTheDocument()
    expect(screen.getByText('action_create_ticket')).toBeInTheDocument()
    expect(screen.getByText('MCP')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /approve/i }))

    await waitFor(() => expect(decideBody).toEqual({ decision: 'approve' }))
    await waitFor(() => expect(screen.queryByText('action_create_ticket')).not.toBeInTheDocument())
  })

  it('records a deny decision for an MCP approval', async () => {
    seedAuth()
    let pending = [makeMcpApproval({ id: 'm2', tool_key: 'add_conversation_note' })]
    let decideBody: unknown
    mockFetch({
      'GET /api/v1/w/w1/ai/approvals': () => ({ body: [] }),
      'GET /api/v1/w/w1/mcp-approvals': () => ({ body: pending }),
      'POST /api/v1/w/w1/mcp-approvals/m2/decide': (init) => {
        decideBody = JSON.parse(init!.body as string)
        pending = []
        return { body: makeMcpApproval({ id: 'm2', status: 'denied' }) }
      },
    })
    renderPage(<ApprovalsPage />)

    expect(await screen.findByText('add_conversation_note')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /deny/i }))

    await waitFor(() => expect(decideBody).toEqual({ decision: 'deny' }))
    await waitFor(() =>
      expect(screen.queryByText('add_conversation_note')).not.toBeInTheDocument()
    )
  })
})
