/**
 * The in-app assistant's round trip through the controller: page context and
 * consent go up, page ops come down, results go back so the agent run resumes.
 *
 * fetch + WebSocket are stubbed; bridge posts are captured by spying on
 * `window.postMessage` (window.parent === window under jsdom), and inbound
 * loader messages are dispatched as `MessageEvent`s — the same seam the
 * campaign tests use.
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

const summary = {
  id: 'conv1',
  status: 'pending',
  last_message_preview: 'hi',
  last_activity_at: new Date().toISOString(),
  unread: false,
}

/** Routes every copilot test needs: boot, open a thread, page-context, results. */
function copilotRoutes(overrides: Route[] = []): Route[] {
  return [
    ...overrides,
    { method: 'POST', path: '/api/widget/boot', body: bootBody },
    { method: 'GET', path: '/conversations/conv1/messages', body: { items: [], next_cursor: null } },
    { method: 'POST', path: '/conversations/conv1/read', body: summary },
    {
      method: 'POST',
      path: '/conversations/conv1/page-context',
      body: { ok: true, page_control: true, allow_actions: false },
    },
    { method: 'GET', path: '/copilot/pending', body: null },
    { method: 'POST', path: '/copilot/result', body: { ok: true, status: 'resumed' } },
  ]
}

let controller: Controller | null = null
let posted: Array<{ type?: string; payload?: Record<string, unknown> }> = []

function makeController(): Controller {
  controller = new Controller({ workspaceKey: 'wk_t', apiBase: 'http://api:8600' })
  return controller
}

function capturePosts(): void {
  posted = []
  vi.spyOn(window, 'postMessage').mockImplementation((data: unknown) => {
    posted.push(data as { type?: string; payload?: Record<string, unknown> })
  })
}

function fromLoader(type: string, payload: unknown): void {
  window.dispatchEvent(
    new MessageEvent('message', { data: envelope(type as never, payload) }),
  )
}

/** Simulate a realtime `copilot.op` frame by driving the socket's callback. */
function pushRealtime(c: Controller, data: Record<string, unknown>): void {
  const socket = (c as unknown as { socket: { onMessage?: (m: unknown) => void } }).socket
  const handler = (socket as unknown as { onMessage: (m: unknown) => void }).onMessage
  handler.call(socket, { type: 'copilot.op', data })
}

afterEach(() => {
  controller?.dispose()
  controller = null
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('page context', () => {
  it('reports the loader’s page to the backend and records what is allowed', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    const calls = mockFetch(copilotRoutes())
    capturePosts()

    const c = makeController()
    await c.boot()
    fromLoader(MSG.PAGE_CONTEXT, {
      url: 'https://app.test/billing',
      path: '/billing',
      title: 'Billing',
    })
    await c.openConversation('conv1')

    await vi.waitFor(() => {
      expect(c.getState().pageControl).toBe(true)
    })
    const context = calls.find((call) => call.url.includes('/page-context'))
    expect(JSON.parse(context!.init.body!)).toEqual({
      url: 'https://app.test/billing',
      title: 'Billing',
      path: '/billing',
    })
    expect(c.getState().page?.title).toBe('Billing')
    expect(c.getState().actionsAllowed).toBe(false)
  })

  it('omits allow_actions on a plain navigation so consent is not revoked', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    const calls = mockFetch(copilotRoutes())
    capturePosts()

    const c = makeController()
    await c.boot()
    fromLoader(MSG.PAGE_CONTEXT, { url: 'https://app.test/', path: '/', title: 'Home' })
    await c.openConversation('conv1')
    fromLoader(MSG.PAGE_CONTEXT, { url: 'https://app.test/invoices', path: '/invoices', title: '' })

    await vi.waitFor(() => {
      expect(calls.filter((call) => call.url.includes('/page-context')).length).toBeGreaterThan(1)
    })
    for (const call of calls.filter((c2) => c2.url.includes('/page-context'))) {
      expect(JSON.parse(call.init.body!)).not.toHaveProperty('allow_actions')
    }
  })

  it('sends consent explicitly when the visitor opts in', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    const calls = mockFetch(
      copilotRoutes([
        {
          method: 'POST',
          path: '/conversations/conv1/page-context',
          body: { ok: true, page_control: true, allow_actions: true },
        },
      ]),
    )
    capturePosts()

    const c = makeController()
    await c.boot()
    fromLoader(MSG.PAGE_CONTEXT, { url: 'https://app.test/', path: '/', title: 'Home' })
    await c.openConversation('conv1')
    await c.setActionsAllowed(true)

    const consent = calls
      .filter((call) => call.url.includes('/page-context'))
      .map((call) => JSON.parse(call.init.body!))
      .find((body) => 'allow_actions' in body)
    expect(consent.allow_actions).toBe(true)
    expect(c.getState().actionsAllowed).toBe(true)
  })

  it('does nothing without an open thread — there is nothing to attach it to', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    const calls = mockFetch(copilotRoutes())
    capturePosts()

    const c = makeController()
    await c.boot()
    fromLoader(MSG.PAGE_CONTEXT, { url: 'https://app.test/', path: '/', title: 'Home' })
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(calls.some((call) => call.url.includes('/page-context'))).toBe(false)
    expect(c.getState().page?.url).toBe('https://app.test/')
  })
})

describe('page ops', () => {
  it('forwards an op to the loader and returns its result to resume the run', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    const calls = mockFetch(copilotRoutes())
    capturePosts()

    const c = makeController()
    await c.boot()
    await c.openConversation('conv1')
    pushRealtime(c, { run_id: 'run1', op_id: 'op1', op: 'snapshot', args: {} })

    const forwarded = posted.find((p) => p.type === MSG.COPILOT_OP)
    expect(forwarded!.payload).toEqual({ opId: 'op1', op: 'snapshot', args: {} })
    expect(c.getState().workingOnPage).toBe('snapshot')

    fromLoader(MSG.COPILOT_RESULT, {
      opId: 'op1',
      result: { ok: true, url: 'https://app.test/', elements: '[0]<button>' },
    })
    await vi.waitFor(() => {
      expect(calls.some((call) => call.url.includes('/copilot/result'))).toBe(true)
    })
    const result = calls.find((call) => call.url.includes('/copilot/result'))!
    expect(JSON.parse(result.init.body!)).toEqual({
      run_id: 'run1',
      op_id: 'op1',
      result: { ok: true, url: 'https://app.test/', elements: '[0]<button>' },
    })
    expect(c.getState().workingOnPage).toBeNull()
  })

  it('plays a recommended tour through the existing tour path', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    const calls = mockFetch(copilotRoutes())
    capturePosts()

    const c = makeController()
    await c.boot()
    await c.openConversation('conv1')
    pushRealtime(c, {
      run_id: 'run2',
      op_id: 'op2',
      op: 'guide',
      args: { tour_id: 'tour-7' },
    })

    expect(posted.find((p) => p.type === MSG.TOUR_START)!.payload).toEqual({ tourId: 'tour-7' })
    await vi.waitFor(() => {
      expect(calls.some((call) => call.url.includes('/copilot/result'))).toBe(true)
    })
    const body = JSON.parse(calls.find((call) => call.url.includes('/copilot/result'))!.init.body!)
    expect(body.result.ok).toBe(true)
  })

  it('reports a missing tour id as a failure the model can read', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    const calls = mockFetch(copilotRoutes())
    capturePosts()

    const c = makeController()
    await c.boot()
    await c.openConversation('conv1')
    pushRealtime(c, { run_id: 'run3', op_id: 'op3', op: 'guide', args: {} })

    await vi.waitFor(() => {
      expect(calls.some((call) => call.url.includes('/copilot/result'))).toBe(true)
    })
    const body = JSON.parse(calls.find((call) => call.url.includes('/copilot/result'))!.init.body!)
    expect(body.result.ok).toBe(false)
    expect(body.result.error).toContain('tour_id')
  })

  it('shows an AI-authored walkthrough through the ad-hoc guide bridge', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    mockFetch(copilotRoutes())
    capturePosts()

    const c = makeController()
    await c.boot()
    await c.openConversation('conv1')
    pushRealtime(c, {
      run_id: 'run4',
      op_id: 'op4',
      op: 'steps',
      args: { title: 'Create an invoice', steps: [{ index: 3, title: 'Click New invoice' }] },
    })

    expect(posted.find((p) => p.type === MSG.GUIDE_START)!.payload).toEqual({
      name: 'Create an invoice',
      steps: [{ index: 3, title: 'Click New invoice' }],
    })
  })

  it('ignores a result for an op it never sent', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    const calls = mockFetch(copilotRoutes())
    capturePosts()

    const c = makeController()
    await c.boot()
    await c.openConversation('conv1')
    fromLoader(MSG.COPILOT_RESULT, { opId: 'never-sent', result: { ok: true } })
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(calls.some((call) => call.url.includes('/copilot/result'))).toBe(false)
  })

  it('ignores a malformed op frame', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    mockFetch(copilotRoutes())
    capturePosts()

    const c = makeController()
    await c.boot()
    await c.openConversation('conv1')
    pushRealtime(c, { run_id: 'run5', op: 'snapshot' })  // no op_id

    expect(posted.some((p) => p.type === MSG.COPILOT_OP)).toBe(false)
    expect(c.getState().workingOnPage).toBeNull()
  })

  it('picks up an op left outstanding by a reload', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    mockFetch(
      copilotRoutes([
        {
          method: 'GET',
          path: '/copilot/pending',
          body: { run_id: 'run6', op_id: 'op6', tool: 'page_read', op: 'read', args: {} },
        },
      ]),
    )
    capturePosts()

    const c = makeController()
    await c.boot()
    await c.openConversation('conv1')

    await vi.waitFor(() => {
      expect(posted.some((p) => p.type === MSG.COPILOT_OP)).toBe(true)
    })
    expect(posted.find((p) => p.type === MSG.COPILOT_OP)!.payload).toEqual({
      opId: 'op6',
      op: 'read',
      args: {},
    })
  })

  it('clears the working indicator even when submitting the result fails', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    mockFetch(
      copilotRoutes([
        { method: 'POST', path: '/copilot/result', body: { detail: 'gone' }, status: 409 },
      ]),
    )
    capturePosts()

    const c = makeController()
    await c.boot()
    await c.openConversation('conv1')
    pushRealtime(c, { run_id: 'run7', op_id: 'op7', op: 'act', args: { index: 0 } })
    expect(c.getState().workingOnPage).toBe('act')

    fromLoader(MSG.COPILOT_RESULT, { opId: 'op7', result: { ok: true } })
    await vi.waitFor(() => {
      expect(c.getState().workingOnPage).toBeNull()
    })
  })
})
