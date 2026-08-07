import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { adoptAccessToken, api, ApiError, refreshSession } from '@/api/client'
import { mockFetch } from '@/test/helpers'
import { useAuthStore } from '@/stores/auth'

describe('api client', () => {
  beforeEach(() => {
    useAuthStore.setState({ accessToken: null })
  })

  it('parses JSON and error envelopes', async () => {
    mockFetch({
      'GET /api/v1/thing': () => ({ body: { ok: true } }),
      'GET /api/v1/missing': () => ({
        status: 404,
        body: { error: { code: 'not_found', message: 'Nope' } },
      }),
    })
    await expect(api.get('/api/v1/thing')).resolves.toEqual({ ok: true })
    const error = (await api.get('/api/v1/missing').catch((e: unknown) => e)) as ApiError
    expect(error).toBeInstanceOf(ApiError)
    expect(error.code).toBe('not_found')
    expect(error.status).toBe(404)
  })

  it('refreshes once on 401 and replays the request', async () => {
    useAuthStore.setState({ accessToken: 'stale' })
    let attempts = 0
    mockFetch({
      'GET /api/v1/protected': (init) => {
        attempts += 1
        const auth = (init?.headers as Record<string, string>)?.Authorization
        if (auth === 'Bearer fresh') return { body: { data: 42 } }
        return { status: 401, body: { error: { code: 'unauthorized', message: 'expired' } } }
      },
      'POST /api/v1/auth/refresh': () => ({ body: { access_token: 'fresh' } }),
    })
    await expect(api.get('/api/v1/protected')).resolves.toEqual({ data: 42 })
    expect(attempts).toBe(2)
    expect(useAuthStore.getState().accessToken).toBe('fresh')
  })

  it('sends bearer token and serializes query params', async () => {
    useAuthStore.setState({ accessToken: 'tok' })
    const fetchMock = mockFetch({ 'GET /api/v1/list': () => ({ body: [] }) })
    await api.get('/api/v1/list', { query: { limit: 5, cursor: undefined, q: 'hi' } })
    const [url, init] = fetchMock.mock.calls[0]!
    expect(String(url)).toContain('limit=5')
    expect(String(url)).toContain('q=hi')
    expect(String(url)).not.toContain('cursor')
    expect((init?.headers as Record<string, string>).Authorization).toBe('Bearer tok')
  })
})

describe('session keep-alive', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    useAuthStore.setState({ accessToken: null })
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('adoptAccessToken stores the token and proactively renews before expiry', async () => {
    const fetchMock = mockFetch({
      'POST /api/v1/auth/refresh': () => ({
        body: { access_token: 'renewed', expires_in: 900 },
      }),
    })
    adoptAccessToken('first', 900)
    expect(useAuthStore.getState().accessToken).toBe('first')

    // The renewal timer is armed at 80% of the TTL (720s), not at expiry.
    await vi.advanceTimersByTimeAsync(719_000)
    expect(fetchMock).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(2_000)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(useAuthStore.getState().accessToken).toBe('renewed')
  })

  it('refreshSession is single-flight per tab', async () => {
    let calls = 0
    mockFetch({
      'POST /api/v1/auth/refresh': () => {
        calls += 1
        return { body: { access_token: `t${calls}`, expires_in: 900 } }
      },
    })
    const [a, b] = await Promise.all([refreshSession(), refreshSession()])
    expect(a).toBe(true)
    expect(b).toBe(true)
    expect(calls).toBe(1)
  })
})
