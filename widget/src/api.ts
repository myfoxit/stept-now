/**
 * Typed fetch client for the public widget API (`/api/widget/*`).
 *
 * Framework-free (no Preact import) so both the iframe app and the host-page
 * loader can use it. The app instantiates {@link WidgetApi} with the contact
 * token from boot; the loader uses the standalone tour helpers, which authorize
 * with the public `widget_key` (+ optional token) instead of the bearer token.
 */

import type {
  BootResult,
  Campaign,
  CampaignTriggerResult,
  ConversationSummary,
  CsatOut,
  CursorPage,
  ArticleDetail,
  FeedbackRating,
  Identity,
  MessageFeedbackAck,
  Tour,
  TourEventName,
  WidgetArticlesResponse,
  WidgetMessage,
} from './types'

export class ApiError extends Error {
  status: number
  body: unknown
  constructor(status: number, message: string, body: unknown = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.body = body
  }
}

/** Trim a trailing slash so `${base}/api/...` never doubles up. */
export function normalizeBase(base: string): string {
  return base.replace(/\/+$/, '')
}

interface RequestOptions {
  method?: string
  body?: string
  token?: string
  headers?: Record<string, string>
}

async function request<T>(base: string, path: string, opts: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { ...opts.headers }
  if (opts.body !== undefined && !('Content-Type' in headers)) {
    headers['Content-Type'] = 'application/json'
  }
  if (opts.token) headers['X-Widget-Token'] = opts.token
  const res = await fetch(`${normalizeBase(base)}${path}`, {
    method: opts.method,
    body: opts.body,
    headers,
  })
  const text = await res.text()
  const data = text ? safeJson(text) : null
  if (!res.ok) {
    throw new ApiError(res.status, errorDetail(data) ?? res.statusText, data)
  }
  return data as T
}

/** Pull FastAPI's `{ "detail": … }` message out of an error body. */
function errorDetail(data: unknown): string | undefined {
  if (data && typeof data === 'object' && 'detail' in data) {
    return String((data as { detail: unknown }).detail)
  }
  return undefined
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

export class WidgetApi {
  readonly base: string
  token: string | null

  constructor(base: string, token: string | null = null) {
    this.base = normalizeBase(base)
    this.token = token
  }

  boot(body: {
    widget_key: string
    visitor_id?: string | null
    identity?: Identity
  }): Promise<BootResult> {
    return request<BootResult>(this.base, '/api/widget/boot', {
      method: 'POST',
      body: JSON.stringify(body),
    })
  }

  listConversations(): Promise<ConversationSummary[]> {
    return this.authed('/api/widget/conversations')
  }

  createConversation(message: string): Promise<ConversationSummary> {
    return this.authed('/api/widget/conversations', {
      method: 'POST',
      body: JSON.stringify({ message }),
    })
  }

  listMessages(conversationId: string, cursor?: string | null): Promise<CursorPage<WidgetMessage>> {
    const q = cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''
    return this.authed(`/api/widget/conversations/${conversationId}/messages${q}`)
  }

  sendMessage(conversationId: string, message: string): Promise<WidgetMessage> {
    return this.authed(`/api/widget/conversations/${conversationId}/messages`, {
      method: 'POST',
      body: JSON.stringify({ message }),
    })
  }

  markRead(conversationId: string): Promise<ConversationSummary> {
    return this.authed(`/api/widget/conversations/${conversationId}/read`, { method: 'POST' })
  }

  async sendTyping(conversationId: string, isTyping: boolean): Promise<void> {
    await this.authed(`/api/widget/conversations/${conversationId}/typing`, {
      method: 'POST',
      body: JSON.stringify({ is_typing: isTyping }),
    })
  }

  getArticles(query = ''): Promise<WidgetArticlesResponse> {
    const q = query.trim() ? `?query=${encodeURIComponent(query.trim())}` : ''
    return this.authed(`/api/widget/articles${q}`)
  }

  getArticle(slug: string): Promise<ArticleDetail> {
    return this.authed(`/api/widget/articles/${encodeURIComponent(slug)}`)
  }

  submitCsat(conversationId: string, rating: number, feedback?: string): Promise<CsatOut> {
    return this.authed(`/api/widget/conversations/${conversationId}/csat`, {
      method: 'POST',
      body: JSON.stringify({ rating, feedback: feedback ?? null }),
    })
  }

  /** Authed request: rejects (never throws synchronously) if boot hasn't run. */
  private authed<T>(path: string, opts: RequestOptions = {}): Promise<T> {
    if (!this.token) return Promise.reject(new ApiError(401, 'Widget not authenticated'))
    return request<T>(this.base, path, { ...opts, token: this.token })
  }
}

/** ws:// URL for the visitor realtime socket. */
export function widgetWsUrl(base: string, token: string): string {
  const url = new URL(`${normalizeBase(base)}/ws/widget`)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  url.searchParams.set('token', token)
  return url.toString()
}

// --- loader-side tour helpers (public widget_key auth) ----------------------

/** GET eligible live tours for the current page. */
export function fetchTours(
  base: string,
  widgetKey: string,
  pageUrl: string,
  token?: string | null,
): Promise<Tour[]> {
  const q = `?widget_key=${encodeURIComponent(widgetKey)}&url=${encodeURIComponent(pageUrl)}`
  const headers: Record<string, string> = {}
  if (token) headers['X-Widget-Token'] = token
  return request<Tour[]>(base, `/api/widget/tours${q}`, { headers })
}

/** POST a tour lifecycle event (started / step_viewed / completed / dismissed). */
export async function postTourEvent(
  base: string,
  widgetKey: string,
  tourId: string,
  event: TourEventName,
  stepIndex: number | null,
  token?: string | null,
): Promise<void> {
  const q = `?widget_key=${encodeURIComponent(widgetKey)}`
  const headers: Record<string, string> = {}
  if (token) headers['X-Widget-Token'] = token
  await request(base, `/api/widget/tours/${tourId}/events${q}`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ event, step_index: stepIndex }),
  })
}

// --- campaigns + answer feedback --------------------------------------------

/** GET the enabled ongoing campaigns for this widget inbox (public key, no token). */
export function fetchCampaigns(base: string, widgetKey: string): Promise<Campaign[]> {
  const q = `?widget_key=${encodeURIComponent(widgetKey)}`
  return request<Campaign[]>(base, `/api/widget/campaigns${q}`)
}

/**
 * POST a due campaign trigger (visitor token). The backend enforces the
 * fresh-visitor semantics and answers `{conversation_id}` or `{skipped: true}`.
 */
export function triggerCampaign(
  base: string,
  token: string,
  campaignId: string,
): Promise<CampaignTriggerResult> {
  return request<CampaignTriggerResult>(base, `/api/widget/campaigns/${campaignId}/trigger`, {
    method: 'POST',
    token,
  })
}

/** POST the visitor's thumbs rating on an agent/AI answer (upserts server-side). */
export function sendMessageFeedback(
  base: string,
  token: string,
  conversationId: string,
  messageId: string,
  rating: FeedbackRating,
): Promise<MessageFeedbackAck> {
  return request<MessageFeedbackAck>(
    base,
    `/api/widget/conversations/${conversationId}/messages/${messageId}/feedback`,
    { method: 'POST', token, body: JSON.stringify({ rating }) },
  )
}
