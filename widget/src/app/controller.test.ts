/**
 * Controller tests for the loader-bridge campaign flow and answer feedback.
 * fetch + WebSocket are stubbed; bridge posts are captured by spying on
 * window.postMessage (window.parent === window under jsdom).
 */

import { afterEach, describe, expect, it, vi } from 'vitest'

import { envelope, MSG } from '../protocol'
import { Controller } from './controller'

class FakeWebSocket {
  static OPEN = 1
  static CONNECTING = 0
  readyState = 0
  close(): void {}
  send(): void {}
}

interface FetchCall {
  url: string
  init: { method?: string; body?: string; headers?: Record<string, string> }
}

type Route = { method: string; path: string; body: unknown; status?: number }

/** Route-matching fetch stub: first route whose method+path-substring matches. */
function mockFetch(routes: Route[]): FetchCall[] {
  const calls: FetchCall[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init: FetchCall['init'] = {}) => {
      calls.push({ url, init })
      const method = init.method ?? 'GET'
      const route = routes.find((r) => r.method === method && url.includes(r.path))
      const status = route?.status ?? (route ? 200 : 404)
      return {
        ok: status < 400,
        status,
        statusText: 'stub',
        text: async () => JSON.stringify(route?.body ?? { detail: 'no route' }),
      } as unknown as Response
    }),
  )
  return calls
}

const bootBody = {
  token: 'tok-1',
  visitor_id: 'v1',
  contact: { id: 'ct1', name: 'Visitor', email: null },
  workspace: { name: 'Acme', logo_url: null },
  config: {},
  conversations: [],
  help_center_enabled: false,
}

const campaignConv = {
  id: 'conv9',
  status: 'open',
  last_message_preview: 'Hey — need a hand?',
  last_activity_at: new Date().toISOString(),
  unread: true,
}

let controller: Controller | null = null

function makeController(): Controller {
  controller = new Controller({ workspaceKey: 'wk_t', apiBase: 'http://api:8600' })
  return controller
}

function dispatchCampaignDue(campaignId: string): void {
  window.dispatchEvent(
    new MessageEvent('message', { data: envelope(MSG.CAMPAIGN_DUE, { campaignId }) }),
  )
}

afterEach(() => {
  controller?.dispose()
  controller = null
  // Note: no localStorage cleanup needed — Node's built-in localStorage global
  // is nonfunctional here and the controller guards every access with try/catch.
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('Controller campaign bridge', () => {
  it('CAMPAIGN_DUE triggers the POST with the token, refreshes the list and bumps unread', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    const calls = mockFetch([
      { method: 'POST', path: '/api/widget/boot', body: bootBody },
      {
        method: 'POST',
        path: '/api/widget/campaigns/camp1/trigger',
        body: { skipped: false, conversation_id: 'conv9' },
      },
      { method: 'GET', path: '/api/widget/conversations', body: [campaignConv] },
    ])
    const posted: Array<{ type?: string; payload?: unknown }> = []
    vi.spyOn(window, 'postMessage').mockImplementation((data: unknown) => {
      posted.push(data as { type?: string; payload?: unknown })
    })

    const c = makeController()
    await c.boot()
    dispatchCampaignDue('camp1')

    await vi.waitFor(() => {
      expect(c.getState().conversations).toHaveLength(1)
    })

    const trigger = calls.find((call) => call.url.includes('/campaigns/camp1/trigger'))
    expect(trigger).toBeDefined()
    expect(trigger!.init.method).toBe('POST')
    expect(trigger!.init.headers!['X-Widget-Token']).toBe('tok-1')

    // The unread badge bump reached the loader via the bridge.
    const unread = posted.filter((p) => p?.type === MSG.UNREAD)
    expect(unread.at(-1)?.payload).toEqual({ count: 1 })
    // Panel is never opened automatically.
    expect(posted.some((p) => p?.type === MSG.OPEN)).toBe(false)
  })

  it('silently ignores skipped triggers without refetching conversations', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    const calls = mockFetch([
      { method: 'POST', path: '/api/widget/boot', body: bootBody },
      {
        method: 'POST',
        path: '/api/widget/campaigns/camp2/trigger',
        body: { skipped: true, conversation_id: null },
      },
      { method: 'GET', path: '/api/widget/conversations', body: [campaignConv] },
    ])
    vi.spyOn(window, 'postMessage').mockImplementation(() => {})

    const c = makeController()
    await c.boot()
    dispatchCampaignDue('camp2')

    await vi.waitFor(() => {
      expect(calls.some((call) => call.url.includes('/campaigns/camp2/trigger'))).toBe(true)
    })
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(c.getState().conversations).toHaveLength(0)
    const listCalls = calls.filter(
      (call) => call.url.endsWith('/api/widget/conversations') && !call.init.method,
    )
    expect(listCalls).toHaveLength(0)
  })
})

describe('Controller.submitMessageFeedback', () => {
  it('POSTs the rating for the open thread with the widget token', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    const calls = mockFetch([
      { method: 'POST', path: '/api/widget/boot', body: bootBody },
      {
        method: 'GET',
        path: '/conversations/conv1/messages',
        body: { items: [], next_cursor: null },
      },
      { method: 'POST', path: '/conversations/conv1/read', body: campaignConv },
      {
        method: 'POST',
        path: '/messages/m5/feedback',
        body: { ok: true, rating: 'down' },
      },
    ])
    vi.spyOn(window, 'postMessage').mockImplementation(() => {})

    const c = makeController()
    await c.boot()
    await c.openConversation('conv1')
    await c.submitMessageFeedback('m5', 'down')

    const fb = calls.find((call) => call.url.includes('/messages/m5/feedback'))
    expect(fb).toBeDefined()
    expect(fb!.url).toBe('http://api:8600/api/widget/conversations/conv1/messages/m5/feedback')
    expect(fb!.init.method).toBe('POST')
    expect(JSON.parse(fb!.init.body!)).toEqual({ rating: 'down' })
    expect(fb!.init.headers!['X-Widget-Token']).toBe('tok-1')
  })

  it('does nothing outside an open thread', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    const calls = mockFetch([{ method: 'POST', path: '/api/widget/boot', body: bootBody }])
    vi.spyOn(window, 'postMessage').mockImplementation(() => {})

    const c = makeController()
    await c.boot() // lands on home, not a thread
    await c.submitMessageFeedback('m5', 'up')

    expect(calls.some((call) => call.url.includes('/feedback'))).toBe(false)
  })
})

describe('failed send + retry', () => {
  async function openThread(c: Controller): Promise<void> {
    await c.boot()
    // Enter a fresh thread screen (conversationId=null); the first send creates it.
    c.startNewConversation()
    await c.send('first')
  }

  it('marks a send failed, then retry re-posts the same text and clears the failure', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    let failNext = false
    const calls = mockFetch([
      { method: 'POST', path: '/api/widget/boot', body: bootBody },
      { method: 'POST', path: '/api/widget/conversations', body: { ...campaignConv, id: 'conv1' } },
      { method: 'GET', path: '/api/widget/conversations/conv1/messages', body: { items: [], next_cursor: null } },
    ])
    vi.spyOn(window, 'postMessage').mockImplementation(() => {})
    const c = makeController()
    await openThread(c)

    // Next reply POST fails.
    failNext = true
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init: FetchCall['init'] = {}) => {
        const isReply = url.includes('/conversations/conv1/messages') && init.method === 'POST'
        if (isReply && failNext) throw new Error('network down')
        return {
          ok: true,
          status: 200,
          statusText: 'stub',
          text: async () => JSON.stringify({ id: 'real1', direction: 'in', author_type: 'contact', author_name: 'You', content: 'hello again', attachments: [], created_at: new Date().toISOString(), meta: {} }),
        } as unknown as Response
      }),
    )

    await c.send('hello again')
    let failed = c.getState().messages.find((m) => m.failed)
    expect(failed?.content).toBe('hello again')

    // Retry succeeds this time.
    failNext = false
    await c.retry(failed!.id)
    expect(c.getState().messages.some((m) => m.failed)).toBe(false)
    expect(c.getState().messages.some((m) => m.id === 'real1')).toBe(true)
    void calls
  })
})

describe('blocked visitors', () => {
  it('shows a neutral message rather than confirming the block', async () => {
    // Telling a blocked visitor they're blocked is hostile and confirms it.
    mockFetch([
      {
        method: 'POST',
        path: '/api/widget/boot',
        status: 403,
        body: { error: { code: 'contact_blocked', message: 'This contact is blocked' } },
      },
    ])
    const c = makeController()
    await c.boot()
    const screen = c.getState().screen
    expect(screen.name).toBe('error')
    expect(screen.name === 'error' && screen.message).toBe('Chat is unavailable right now.')
  })
})
