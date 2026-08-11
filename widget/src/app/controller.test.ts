/**
 * Controller tests for the loader-bridge campaign flow and answer feedback.
 * fetch + WebSocket are stubbed; bridge posts are captured by spying on
 * window.postMessage (window.parent === window under jsdom).
 */

import { afterEach, describe, expect, it, vi } from 'vitest'

import { envelope, MSG, type MessageType } from '../protocol'
import { MSG_EXTRA } from './api-extra'
import { Controller } from './controller'

class FakeWebSocket {
  static OPEN = 1
  static CONNECTING = 0
  /** Last instance, so tests can push frames through `onmessage`. */
  static last: FakeWebSocket | null = null
  readyState = 0
  onmessage: ((event: { data: string }) => void) | null = null
  constructor() {
    FakeWebSocket.last = this
  }
  close(): void {}
  send(): void {}
}

/** Deliver a realtime frame as if it came over the visitor websocket. */
function pushRealtime(frame: unknown): void {
  FakeWebSocket.last?.onmessage?.({ data: JSON.stringify(frame) })
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

// --- unread → thread routing -------------------------------------------------

const unreadConv = (id: string) => ({
  id,
  status: 'open',
  last_message_preview: 'Which plan are you on?',
  last_activity_at: new Date().toISOString(),
  unread: true,
})

function dispatchOpen(): void {
  window.dispatchEvent(new MessageEvent('message', { data: envelope(MSG.OPEN, {}) }))
}

describe('opening the panel with unread replies', () => {
  it('lands directly in the single conversation with unread messages', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    mockFetch([
      { method: 'POST', path: '/api/widget/boot', body: bootBody },
      { method: 'GET', path: '/conversations/c1/messages', body: { items: [], next_cursor: null } },
      { method: 'POST', path: '/conversations/c1/read', body: { ...unreadConv('c1'), unread: false } },
      { method: 'GET', path: '/api/widget/conversations', body: [unreadConv('c1')] },
    ])
    vi.spyOn(window, 'postMessage').mockImplementation(() => {})

    const c = makeController()
    await c.boot()
    expect(c.getState().screen).toEqual({ name: 'home' })

    dispatchOpen()
    await vi.waitFor(() => {
      expect(c.getState().screen).toEqual({ name: 'thread', conversationId: 'c1' })
    })
  })

  it('stays on Home when several conversations are unread', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    mockFetch([
      { method: 'POST', path: '/api/widget/boot', body: bootBody },
      {
        method: 'GET',
        path: '/api/widget/conversations',
        body: [unreadConv('c1'), unreadConv('c2')],
      },
    ])
    vi.spyOn(window, 'postMessage').mockImplementation(() => {})

    const c = makeController()
    await c.boot()
    dispatchOpen()
    await vi.waitFor(() => {
      expect(c.getState().conversations).toHaveLength(2)
    })
    expect(c.getState().screen).toEqual({ name: 'home' })
  })

  it('routes after boot when the panel opened before boot finished', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    let releaseBoot: (() => void) | null = null
    const gate = new Promise<void>((resolve) => (releaseBoot = resolve))
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (url.includes('/api/widget/boot')) await gate
        const body = url.includes('/boot')
          ? { ...bootBody, conversations: [unreadConv('c1')] }
          : url.includes('/messages')
            ? { items: [], next_cursor: null }
            : url.includes('/read')
              ? { ...unreadConv('c1'), unread: false }
              : [unreadConv('c1')]
        return {
          ok: true,
          status: 200,
          statusText: 'stub',
          text: async () => JSON.stringify(body),
        } as unknown as Response
      }),
    )
    vi.spyOn(window, 'postMessage').mockImplementation(() => {})

    const c = makeController()
    const booting = c.boot()
    dispatchOpen() // visitor clicks the launcher while boot is in flight
    releaseBoot!()
    await booting
    await vi.waitFor(() => {
      expect(c.getState().screen).toEqual({ name: 'thread', conversationId: 'c1' })
    })
  })
})

// --- stale "working on the page" status --------------------------------------

describe('working-on-page staleness timeout', () => {
  it('clears the status line when no update arrives for 90s', async () => {
    vi.useFakeTimers()
    try {
      vi.stubGlobal('WebSocket', FakeWebSocket)
      mockFetch([
        { method: 'POST', path: '/api/widget/boot', body: bootBody },
        {
          method: 'GET',
          path: '/conversations/conv1/messages',
          body: { items: [], next_cursor: null },
        },
        { method: 'POST', path: '/conversations/conv1/read', body: campaignConv },
        {
          method: 'GET',
          path: '/conversations/conv1/copilot/pending',
          body: { run_id: 'r1', op_id: 'op1', tool: 'page_read', op: 'snapshot', args: {} },
        },
      ])
      vi.spyOn(window, 'postMessage').mockImplementation(() => {})

      const c = makeController()
      await c.boot()
      await c.openConversation('conv1')
      // resumePendingOp is fire-and-forget; flush it.
      await vi.advanceTimersByTimeAsync(0)
      expect(c.getState().workingOnPage).toBe('snapshot')

      await vi.advanceTimersByTimeAsync(89_000)
      expect(c.getState().workingOnPage).toBe('snapshot')
      await vi.advanceTimersByTimeAsync(2_000)
      expect(c.getState().workingOnPage).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })
})

// --- human handoff ------------------------------------------------------------

describe('requestHuman', () => {
  it('sends the handoff message and remembers the pending-human state', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    const calls = mockFetch([
      { method: 'POST', path: '/api/widget/boot', body: bootBody },
      { method: 'POST', path: '/api/widget/conversations', body: { ...campaignConv, id: 'conv1' } },
      {
        method: 'GET',
        path: '/api/widget/conversations/conv1/messages',
        body: { items: [], next_cursor: null },
      },
    ])
    vi.spyOn(window, 'postMessage').mockImplementation(() => {})

    const c = makeController()
    await c.boot()
    c.startNewConversation()
    await c.requestHuman()

    const create = calls.find(
      (call) => call.url.endsWith('/api/widget/conversations') && call.init.method === 'POST',
    )
    expect(create).toBeDefined()
    expect(JSON.parse(create!.init.body!).message).toBe('I’d like to talk to a person.')
    expect(c.getState().humanRequested['conv1']).toBe(true)
  })
})

// --- live tour state ----------------------------------------------------------

describe('tour state from the loader', () => {
  it('consumes legacy TOUR_EVENT frames (stepIndex → 1-based step)', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    mockFetch([{ method: 'POST', path: '/api/widget/boot', body: bootBody }])
    vi.spyOn(window, 'postMessage').mockImplementation(() => {})

    const c = makeController()
    await c.boot()
    window.dispatchEvent(
      new MessageEvent('message', {
        data: envelope(MSG.TOUR_EVENT, { tourId: 't1', event: 'started', stepIndex: 0 }),
      }),
    )
    expect(c.getState().tourState).toMatchObject({ status: 'started', tourId: 't1', step: 1 })
  })

  it('consumes tour:state frames and keeps the known title on later updates', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    mockFetch([{ method: 'POST', path: '/api/widget/boot', body: bootBody }])
    vi.spyOn(window, 'postMessage').mockImplementation(() => {})

    const c = makeController()
    await c.boot()
    const state = (payload: unknown) =>
      window.dispatchEvent(
        new MessageEvent('message', {
          data: envelope(MSG_EXTRA.TOUR_STATE as unknown as MessageType, payload),
        }),
      )
    state({ status: 'started', tourId: 't1', step: 1, total: 5, title: 'Widget setup' })
    state({ status: 'blocked', tourId: 't1', step: 3 })
    expect(c.getState().tourState).toMatchObject({
      status: 'blocked',
      tourId: 't1',
      step: 3,
      total: 5,
      title: 'Widget setup',
    })
  })
})

// --- tour_event messages must stay ambient ------------------------------------

describe('tour_event realtime messages', () => {
  it('never becomes the row preview nor an unread ping', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    const readConv = {
      ...campaignConv,
      id: 'c1',
      unread: false,
      last_message_preview: 'All good so far',
    }
    mockFetch([
      { method: 'POST', path: '/api/widget/boot', body: { ...bootBody, conversations: [readConv] } },
    ])
    vi.spyOn(window, 'postMessage').mockImplementation(() => {})

    const c = makeController()
    await c.boot()
    pushRealtime({
      type: 'message.created',
      data: {
        message: {
          id: 'ev1',
          conversation_id: 'c1',
          visibility: 'public',
          direction: 'out',
          author_type: 'system',
          author_name: '',
          content: '✕ Dismissed at step 2',
          attachments: [{ kind: 'tour_event', event: 'dismissed', step: 2 }],
          created_at: new Date().toISOString(),
          meta: {},
        },
      },
    })
    const conv = c.getState().conversations[0]!
    expect(conv.unread).toBe(false)
    expect(conv.last_message_preview).toBe('All good so far')

    // A real reply still pings as before.
    pushRealtime({
      type: 'message.created',
      data: {
        message: {
          id: 'm2',
          conversation_id: 'c1',
          visibility: 'public',
          direction: 'out',
          author_type: 'agent',
          author_name: 'Sage',
          content: 'Here is the answer',
          attachments: [],
          created_at: new Date().toISOString(),
          meta: {},
        },
      },
    })
    expect(c.getState().conversations[0]!.unread).toBe(true)
    expect(c.getState().conversations[0]!.last_message_preview).toBe('Here is the answer')
  })
})

// --- federated search ----------------------------------------------------------

describe('searchEverything', () => {
  it('merges article results with name-matched live tours for the page', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    mockFetch([
      { method: 'POST', path: '/api/widget/boot', body: bootBody },
      {
        method: 'GET',
        path: '/api/widget/articles',
        body: {
          collections: [],
          results: [{ title: 'Set up your workspace', slug: 'setup', snippet: 'How to begin' }],
        },
      },
      {
        method: 'GET',
        path: '/api/widget/tours',
        body: [
          { id: 't1', name: 'Setup walkthrough', steps: [{}, {}, {}], theme: {}, version: 1 },
          { id: 't2', name: 'Billing overview', steps: [{}], theme: {}, version: 1 },
        ],
      },
    ])
    vi.spyOn(window, 'postMessage').mockImplementation(() => {})

    const c = makeController()
    await c.boot()
    // The loader reported the page — tours are fetched for it.
    window.dispatchEvent(
      new MessageEvent('message', {
        data: envelope(MSG.PAGE_CONTEXT, { url: 'https://app.test/settings', path: '/settings', title: 'Settings' }),
      }),
    )
    await c.searchEverything('setup')

    const search = c.getState().homeSearch
    expect(search).not.toBeNull()
    expect(search!.loading).toBe(false)
    expect(search!.articles).toEqual([
      { title: 'Set up your workspace', slug: 'setup', snippet: 'How to begin' },
    ])
    // Only the tour whose name matches the query, with its step count.
    expect(search!.tours).toEqual([{ id: 't1', name: 'Setup walkthrough', steps: 3 }])
  })

  it('clears results when the query empties', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    mockFetch([
      { method: 'POST', path: '/api/widget/boot', body: bootBody },
      { method: 'GET', path: '/api/widget/articles', body: { collections: [], results: [] } },
    ])
    vi.spyOn(window, 'postMessage').mockImplementation(() => {})

    const c = makeController()
    await c.boot()
    await c.searchEverything('x')
    expect(c.getState().homeSearch).not.toBeNull()
    await c.searchEverything('')
    expect(c.getState().homeSearch).toBeNull()
  })
})

// --- starting tours -------------------------------------------------------------

describe('startTour', () => {
  it('posts tour:start and leaves panel choreography to the loader', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    mockFetch([{ method: 'POST', path: '/api/widget/boot', body: bootBody }])
    const posted: Array<{ type?: string; payload?: unknown }> = []
    vi.spyOn(window, 'postMessage').mockImplementation((data: unknown) => {
      posted.push(data as { type?: string; payload?: unknown })
    })

    const c = makeController()
    await c.boot()
    c.startTour('t42')

    const start = posted.find((p) => p?.type === MSG.TOUR_START)
    expect(start?.payload).toEqual({ tourId: 't42' })
    // The loader collapses the panel to its pill and restores it after the
    // tour — the app must not post CLOSE and forfeit that restore.
    expect(posted.some((p) => p?.type === MSG.CLOSE)).toBe(false)
  })
})
