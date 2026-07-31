import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, fetchTours, normalizeBase, WidgetApi, widgetWsUrl } from './api'

interface FetchCall {
  url: string
  init: { method?: string; body?: string; headers?: Record<string, string> }
}

function mockFetch(response: { ok?: boolean; status?: number; body?: unknown; statusText?: string }) {
  const calls: FetchCall[] = []
  const fn = vi.fn(async (url: string, init: FetchCall['init'] = {}) => {
    calls.push({ url, init })
    return {
      ok: response.ok ?? true,
      status: response.status ?? 200,
      statusText: response.statusText ?? 'OK',
      text: async () => (response.body === undefined ? '' : JSON.stringify(response.body)),
    } as unknown as Response
  })
  vi.stubGlobal('fetch', fn)
  return calls
}

afterEach(() => vi.unstubAllGlobals())

describe('normalizeBase', () => {
  it('strips trailing slashes', () => {
    expect(normalizeBase('http://x:8600/')).toBe('http://x:8600')
    expect(normalizeBase('http://x:8600///')).toBe('http://x:8600')
  })
})

describe('WidgetApi', () => {
  it('POSTs boot without a token and with the widget_key body', async () => {
    const calls = mockFetch({ body: { token: 't', visitor_id: 'v1' } })
    const api = new WidgetApi('http://api:8600/')
    await api.boot({ widget_key: 'wk_abc', visitor_id: 'v1' })
    expect(calls).toHaveLength(1)
    expect(calls[0]!.url).toBe('http://api:8600/api/widget/boot')
    expect(calls[0]!.init.method).toBe('POST')
    expect(JSON.parse(calls[0]!.init.body!)).toEqual({ widget_key: 'wk_abc', visitor_id: 'v1' })
    expect(calls[0]!.init.headers).not.toHaveProperty('X-Widget-Token')
  })

  it('sends the bearer widget token on authed calls', async () => {
    const calls = mockFetch({ body: { id: 'm1' } })
    const api = new WidgetApi('http://api:8600', 'tok-123')
    await api.sendMessage('conv1', 'hello')
    expect(calls[0]!.url).toBe('http://api:8600/api/widget/conversations/conv1/messages')
    expect(calls[0]!.init.headers!['X-Widget-Token']).toBe('tok-123')
  })

  it('throws ApiError carrying status + detail on non-2xx', async () => {
    mockFetch({ ok: false, status: 404, body: { detail: 'Unknown or disabled widget' } })
    const api = new WidgetApi('http://api:8600', 'tok')
    await expect(api.listConversations()).rejects.toMatchObject({
      name: 'ApiError',
      status: 404,
      message: 'Unknown or disabled widget',
    })
  })

  it('refuses authed calls without a token', async () => {
    mockFetch({ body: [] })
    const api = new WidgetApi('http://api:8600')
    await expect(api.listConversations()).rejects.toBeInstanceOf(ApiError)
  })
})

describe('tour helpers', () => {
  it('fetchTours passes widget_key + url as query params', async () => {
    const calls = mockFetch({ body: [] })
    await fetchTours('http://api:8600', 'wk_x', 'https://site.test/pricing', 'tok')
    const url = new URL(calls[0]!.url)
    expect(url.pathname).toBe('/api/widget/tours')
    expect(url.searchParams.get('widget_key')).toBe('wk_x')
    expect(url.searchParams.get('url')).toBe('https://site.test/pricing')
    expect(calls[0]!.init.headers!['X-Widget-Token']).toBe('tok')
  })
})

describe('widgetWsUrl', () => {
  it('rewrites http→ws and appends the token', () => {
    expect(widgetWsUrl('http://localhost:8600', 'abc')).toBe('ws://localhost:8600/ws/widget?token=abc')
  })
  it('rewrites https→wss', () => {
    expect(widgetWsUrl('https://stept.io', 'xy')).toBe('wss://stept.io/ws/widget?token=xy')
  })
})
