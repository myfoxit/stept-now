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

let refreshPromise: Promise<boolean> | null = null

async function tryRefresh(): Promise<boolean> {
  refreshPromise ??= (async () => {
    try {
      const response = await fetch(`${BASE}/api/v1/auth/refresh`, {
        method: 'POST',
        credentials: 'include',
      })
      if (!response.ok) return false
      const data = await response.json()
      useAuthStore.getState().setAccessToken(data.access_token)
      return true
    } catch {
      return false
    } finally {
      refreshPromise = null
    }
  })()
  return refreshPromise
}

function buildUrl(path: string, query?: RequestOptions['query']): string {
  const url = new URL(`${BASE}${path}`, window.location.origin)
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== null && value !== '') url.searchParams.set(key, String(value))
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
    const err = (payload as { error?: { code?: string; message?: string; details?: unknown } })?.error
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
export function ws(path: string): string {
  const id = useAuthStore.getState().workspaceId
  if (!id) throw new Error('No active workspace selected')
  return `/api/v1/w/${id}${path}`
}
