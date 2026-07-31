import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  ApiError,
  fetchCampaigns,
  fetchExperiences,
  fetchPreviewTour,
  fetchTour,
  fetchTours,
  normalizeBase,
  postChecklistProgress,
  postSurveyResponse,
  postTourEvent,
  sendMessageFeedback,
  triggerCampaign,
  WidgetApi,
  widgetWsUrl,
} from './api'

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

describe('DAP helpers', () => {
  it('fetchExperiences hits the one-call bootstrap with widget_key + url', async () => {
    const calls = mockFetch({ body: { tours: [], checklists: [], surveys: [] } })
    const data = await fetchExperiences('http://api:8600', 'wk_x', 'https://site.test/app', 'tok')
    const url = new URL(calls[0]!.url)
    expect(url.pathname).toBe('/api/widget/experiences')
    expect(url.searchParams.get('widget_key')).toBe('wk_x')
    expect(url.searchParams.get('url')).toBe('https://site.test/app')
    expect(calls[0]!.init.headers!['X-Widget-Token']).toBe('tok')
    expect(data).toEqual({ tours: [], checklists: [], surveys: [] })
  })

  it('fetchTour resolves ONE tour by id — the manual-trigger start path', async () => {
    const calls = mockFetch({ body: { id: 't1', name: 'T', steps: [] } })
    await fetchTour('http://api:8600', 'wk_x', 't1', null)
    const url = new URL(calls[0]!.url)
    expect(url.pathname).toBe('/api/widget/tours/t1')
    expect(url.searchParams.get('widget_key')).toBe('wk_x')
    expect(url.searchParams.has('url')).toBe(false)
    expect(calls[0]!.init.headers ?? {}).not.toHaveProperty('X-Widget-Token')
  })

  it('fetchPreviewTour authenticates with the preview token only', async () => {
    const calls = mockFetch({ body: { id: 't1', name: 'T', steps: [] } })
    await fetchPreviewTour('http://api:8600', 't1', 'pv-token')
    const url = new URL(calls[0]!.url)
    expect(url.pathname).toBe('/api/widget/tours/t1')
    expect(url.searchParams.get('preview_token')).toBe('pv-token')
    expect(url.searchParams.has('widget_key')).toBe(false)
  })

  it('postTourEvent carries the step index and telemetry meta', async () => {
    const calls = mockFetch({ body: { message: 'recorded' } })
    await postTourEvent('http://api:8600', 'wk_x', 't1', 'step_error', 2, 'tok', {
      url: 'https://site.test/app',
      viewport_w: 1280,
      reason: 'not_found',
    })
    expect(new URL(calls[0]!.url).pathname).toBe('/api/widget/tours/t1/events')
    expect(JSON.parse(calls[0]!.init.body!)).toEqual({
      event: 'step_error',
      step_index: 2,
      meta: { url: 'https://site.test/app', viewport_w: 1280, reason: 'not_found' },
    })
  })

  it('postChecklistProgress posts {item_id, done} for one item', async () => {
    const calls = mockFetch({ body: { stored: false } })
    const ack = await postChecklistProgress('http://api:8600', 'wk_x', 'cl1', 'i2', true, null)
    expect(new URL(calls[0]!.url).pathname).toBe('/api/widget/checklists/cl1/progress')
    expect(JSON.parse(calls[0]!.init.body!)).toEqual({ item_id: 'i2', done: true })
    expect(ack.stored).toBe(false)
  })

  it('postSurveyResponse posts the answers, the completed flag and the page url', async () => {
    const calls = mockFetch({ body: { ok: true, thanks_message: 'Thanks!' } })
    await postSurveyResponse(
      'http://api:8600',
      'wk_x',
      'sv1',
      [{ question_id: 'q1', value: 9 }],
      false,
      'tok',
      'https://site.test/app',
    )
    const url = new URL(calls[0]!.url)
    expect(url.pathname).toBe('/api/widget/surveys/sv1/responses')
    expect(url.searchParams.get('url')).toBe('https://site.test/app')
    expect(JSON.parse(calls[0]!.init.body!)).toEqual({
      answers: [{ question_id: 'q1', value: 9 }],
      completed: false,
    })
  })
})

describe('campaign helpers', () => {
  it('fetchCampaigns GETs the public list with widget_key only (no token header)', async () => {
    const calls = mockFetch({ body: [] })
    await fetchCampaigns('http://api:8600/', 'wk_x')
    const url = new URL(calls[0]!.url)
    expect(url.pathname).toBe('/api/widget/campaigns')
    expect(url.searchParams.get('widget_key')).toBe('wk_x')
    expect(calls[0]!.init.method).toBeUndefined()
    expect(calls[0]!.init.headers ?? {}).not.toHaveProperty('X-Widget-Token')
  })

  it('triggerCampaign POSTs with the widget token header and parses the result', async () => {
    const calls = mockFetch({ body: { skipped: false, conversation_id: 'conv9' } })
    const result = await triggerCampaign('http://api:8600', 'tok-1', 'camp1')
    expect(calls[0]!.url).toBe('http://api:8600/api/widget/campaigns/camp1/trigger')
    expect(calls[0]!.init.method).toBe('POST')
    expect(calls[0]!.init.headers!['X-Widget-Token']).toBe('tok-1')
    expect(result).toEqual({ skipped: false, conversation_id: 'conv9' })
  })
})

describe('sendMessageFeedback', () => {
  it('POSTs the rating body to the message feedback endpoint with the token', async () => {
    const calls = mockFetch({ body: { ok: true, rating: 'up' } })
    await sendMessageFeedback('http://api:8600', 'tok-2', 'conv1', 'm7', 'up')
    expect(calls[0]!.url).toBe(
      'http://api:8600/api/widget/conversations/conv1/messages/m7/feedback',
    )
    expect(calls[0]!.init.method).toBe('POST')
    expect(JSON.parse(calls[0]!.init.body!)).toEqual({ rating: 'up' })
    expect(calls[0]!.init.headers!['X-Widget-Token']).toBe('tok-2')
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
