/**
 * Typed API client. Access token lives in memory (auth store); on 401 we try one
 * cookie-based refresh and replay the request. All non-2xx responses throw ApiError.
 */

import { useAuthStore } from '@/stores/auth'

const BASE = import.meta.env.VITE_API_BASE_URL ?? ''

export class ApiError extends Error {
  status: number
  code: string
  details?: unknown

  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message)
    this.status = status
    this.code = code
    this.details = details
  }
}

interface RequestOptions {
  body?: unknown
  formData?: FormData
  query?: Record<string, string | number | boolean | undefined | null>
  signal?: AbortSignal
  /** skip the automatic refresh-and-retry (used by auth endpoints themselves) */
  noRetry?: boolean
}

/**
 * Session keep-alive.
 *
 * The access token lives only in JS memory, so staying logged in hinges on the
 * refresh flow being race-free:
 *  - a Web Locks mutex serializes refreshes across tabs (they share one rotating
 *    cookie — concurrent rotation is what used to trip server-side theft
 *    detection and log every tab out),
 *  - a BroadcastChannel hands fresh tokens to sibling tabs so they never need
 *    their own refresh round-trip,
 *  - a proactive timer renews at ~80% of the token's lifetime, with a
 *    visibilitychange catch-up for laptops waking from sleep.
 */

const authChannel: BroadcastChannel | null =
  typeof BroadcastChannel === 'undefined' ? null : new BroadcastChannel('stept-auth')

let tokenExpiresAt = 0
let refreshTimer: ReturnType<typeof setTimeout> | null = null

/** Adopt a freshly-issued access token: store it, arm renewal, tell other tabs. */
export function adoptAccessToken(token: string, expiresInSeconds?: number, broadcast = true): void {
  useAuthStore.getState().setAccessToken(token)
  const ttl = expiresInSeconds ?? 900
  tokenExpiresAt = Date.now() + ttl * 1000
  armProactiveRefresh(ttl)
  if (broadcast) authChannel?.postMessage({ type: 'token', token, expiresIn: ttl })
}

function armProactiveRefresh(ttlSeconds: number): void {
  if (refreshTimer) clearTimeout(refreshTimer)
  refreshTimer = setTimeout(() => void refreshSession(), Math.max(30, ttlSeconds * 0.8) * 1000)
}

authChannel?.addEventListener('message', (event) => {
  const msg = event.data as { type?: string; token?: string; expiresIn?: number }
  if (msg?.type === 'token' && msg.token) {
    adoptAccessToken(msg.token, msg.expiresIn, false)
  } else if (msg?.type === 'logout') {
    if (refreshTimer) clearTimeout(refreshTimer)
    tokenExpiresAt = 0
    useAuthStore.getState().clear()
    window.location.assign('/login')
  }
})

if (typeof document !== 'undefined') {
  document.addEventListener('visibilitychange', () => {
    // Timers are throttled/suspended in background tabs and during sleep; on
    // wake, renew if the token is past (or near) its proactive deadline.
    const token = useAuthStore.getState().accessToken
    if (document.visibilityState === 'visible' && token && Date.now() > tokenExpiresAt - 120_000) {
      void refreshSession()
    }
  })
}

/** Notify every tab (including this one’s state) that the session ended. */
export function announceLogout(): void {
  if (refreshTimer) clearTimeout(refreshTimer)
  tokenExpiresAt = 0
  authChannel?.postMessage({ type: 'logout' })
}

let refreshPromise: Promise<boolean> | null = null

async function doRefresh(): Promise<boolean> {
  try {
    const response = await fetch(`${BASE}/api/v1/auth/refresh`, {
      method: 'POST',
      credentials: 'include',
    })
    if (!response.ok) return false
    const data = await response.json()
    adoptAccessToken(data.access_token, data.expires_in)
    return true
  } catch {
    return false
  }
}

/** Single-flight (per tab) + Web-Locks (across tabs) cookie refresh. */
export async function refreshSession(): Promise<boolean> {
  refreshPromise ??= (async () => {
    try {
      const locks = typeof navigator !== 'undefined' ? navigator.locks : undefined
      if (locks?.request) {
        return await locks.request('stept-session-refresh', async () => {
          // Another tab may have refreshed while we waited on the lock and
          // broadcast us a token that is still comfortably fresh.
          const current = useAuthStore.getState().accessToken
          if (current && Date.now() < tokenExpiresAt - 60_000) return true
          return doRefresh()
        })
      }
      return await doRefresh()
    } finally {
      refreshPromise = null
    }
  })()
  return refreshPromise
}

const tryRefresh = refreshSession

function buildUrl(path: string, query?: RequestOptions['query']): string {
  const url = new URL(`${BASE}${path}`, window.location.origin)
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== null && value !== '')
        url.searchParams.set(key, String(value))
    }
  }
  return url.pathname + url.search
}

async function request<T>(method: string, path: string, options: RequestOptions = {}): Promise<T> {
  const execute = async (): Promise<Response> => {
    const headers: Record<string, string> = {}
    const token = useAuthStore.getState().accessToken
    if (token) headers.Authorization = `Bearer ${token}`
    let body: BodyInit | undefined
    if (options.formData) {
      body = options.formData
    } else if (options.body !== undefined) {
      headers['Content-Type'] = 'application/json'
      body = JSON.stringify(options.body)
    }
    return fetch(buildUrl(path, options.query), {
      method,
      headers,
      body,
      credentials: 'include',
      signal: options.signal,
    })
  }

  let response = await execute()
  if (response.status === 401 && !options.noRetry && (await tryRefresh())) {
    response = await execute()
  }

  if (response.status === 204) return undefined as T

  let payload: unknown
  const text = await response.text()
  try {
    payload = text ? JSON.parse(text) : undefined
  } catch {
    payload = text
  }

  if (!response.ok) {
    const err = (payload as { error?: { code?: string; message?: string; details?: unknown } })
      ?.error
    throw new ApiError(
      response.status,
      err?.code ?? 'unknown_error',
      err?.message ?? `Request failed (${response.status})`,
      err?.details
    )
  }
  return payload as T
}

export const api = {
  get: <T>(path: string, options?: RequestOptions) => request<T>('GET', path, options),
  post: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>('POST', path, { ...options, body }),
  patch: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>('PATCH', path, { ...options, body }),
  put: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>('PUT', path, { ...options, body }),
  delete: <T>(path: string, options?: RequestOptions) => request<T>('DELETE', path, options),
  upload: <T>(path: string, file: File, field = 'file') => {
    const formData = new FormData()
    formData.append(field, file)
    return request<T>('POST', path, { formData })
  },
}

/** Workspace-scoped path helper: ws('/conversations') → /api/v1/w/{id}/conversations */
/** Auth header for requests that bypass `api.*` (e.g. blob downloads). */
export function authHeaders(): Record<string, string> {
  const token = useAuthStore.getState().accessToken
  return token ? { Authorization: `Bearer ${token}` } : {}
}

export function ws(path: string): string {
  const id = useAuthStore.getState().workspaceId
  if (!id) throw new Error('No active workspace selected')
  return `/api/v1/w/${id}${path}`
}
