/**
 * Typed fetch client for the public widget API (`/api/widget/*`).
 *
 * Framework-free (no Preact import) so both the iframe app and the host-page
 * loader can use it. The app instantiates {@link WidgetApi} with the contact
 * token from boot; the loader uses the standalone tour helpers, which authorize
 * with the public `widget_key` (+ optional token) instead of the bearer token.
 */

import type { ClientActionWireDef } from './actions'
import type {
  BootResult,
  Campaign,
  CampaignTriggerResult,
  ChecklistProgressAck,
  ConversationSummary,
  CopilotOpAck,
  CsatOut,
  CursorPage,
  ArticleDetail,
  ExperiencesResponse,
  FeedbackRating,
  Identity,
  MessageFeedbackAck,
  PageContextAck,
  PendingPageOp,
  SurveyAck,
  SurveyAnswer,
  Tour,
  TourEventMeta,
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
    locale?: string
  }): Promise<BootResult> {
    return request<BootResult>(this.base, '/api/widget/boot', {
      method: 'POST',
      body: JSON.stringify(body),
    })
  }

  listConversations(): Promise<ConversationSummary[]> {
    return this.authed('/api/widget/conversations')
  }

  createConversation(
    message: string,
    clientActions?: ClientActionWireDef[],
  ): Promise<ConversationSummary> {
    return this.authed('/api/widget/conversations', {
      method: 'POST',
      body: JSON.stringify({
        message,
        // With the message on purpose: stored in the same transaction that
        // triggers the agent run, so the FIRST turn already has the page's verbs.
        ...(clientActions === undefined ? {} : { client_actions: clientActions }),
      }),
    })
  }

  listMessages(conversationId: string, cursor?: string | null): Promise<CursorPage<WidgetMessage>> {
    const q = cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''
    return this.authed(`/api/widget/conversations/${conversationId}/messages${q}`)
  }

  sendMessage(
    conversationId: string,
    message: string,
    clientActions?: ClientActionWireDef[],
  ): Promise<WidgetMessage> {
    return this.authed(`/api/widget/conversations/${conversationId}/messages`, {
      method: 'POST',
      body: JSON.stringify({
        message,
        ...(clientActions === undefined ? {} : { client_actions: clientActions }),
      }),
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

  /**
   * Tell the backend where the visitor is, and (when `allowActions` is given)
   * whether the assistant may act on the page.
   *
   * `allowActions` is deliberately tri-state: omitted leaves an earlier answer
   * untouched, which matters because this is re-sent on every SPA navigation and
   * must not silently revoke consent the visitor already gave.
   */
  setPageContext(
    conversationId: string,
    context: {
      url: string
      title?: string
      path?: string
      allowActions?: boolean
      /** Tri-state like allowActions: omitted leaves the stored defs untouched. */
      clientActions?: ClientActionWireDef[]
    },
  ): Promise<PageContextAck> {
    return this.authed(`/api/widget/conversations/${conversationId}/page-context`, {
      method: 'POST',
      body: JSON.stringify({
        url: context.url,
        title: context.title ?? null,
        path: context.path ?? null,
        ...(context.allowActions === undefined ? {} : { allow_actions: context.allowActions }),
        ...(context.clientActions === undefined ? {} : { client_actions: context.clientActions }),
      }),
    })
  }

  /** Any page op this conversation is waiting on — polled once after a reload. */
  getPendingOp(conversationId: string): Promise<PendingPageOp | null> {
    return this.authed(`/api/widget/conversations/${conversationId}/copilot/pending`)
  }

  /** Hand a page-op result back so the parked agent run can continue. */
  submitOpResult(
    conversationId: string,
    body: { run_id: string; op_id: string; result: unknown },
  ): Promise<CopilotOpAck> {
    return this.authed(`/api/widget/conversations/${conversationId}/copilot/result`, {
      method: 'POST',
      body: JSON.stringify(body),
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

// --- loader-side DAP helpers (public widget_key auth) -----------------------

/** `X-Widget-Token` header when the visitor is identified, else nothing. */
function widgetHeaders(token?: string | null): Record<string, string> {
  return token ? { 'X-Widget-Token': token } : {}
}

/**
 * GET every deliverable experience for this page + visitor in one round trip
 * (tours, checklists, surveys). This is the widget's DAP bootstrap — it
 * replaces the per-kind polling `fetchTours` did on its own.
 */
export function fetchExperiences(
  base: string,
  widgetKey: string,
  pageUrl: string,
  token?: string | null,
): Promise<ExperiencesResponse> {
  const q = `?widget_key=${encodeURIComponent(widgetKey)}&url=${encodeURIComponent(pageUrl)}`
  return request<ExperiencesResponse>(base, `/api/widget/experiences${q}`, {
    headers: widgetHeaders(token),
  })
}

/** GET eligible live tours for the current page (legacy single-kind bootstrap). */
export function fetchTours(
  base: string,
  widgetKey: string,
  pageUrl: string,
  token?: string | null,
): Promise<Tour[]> {
  const q = `?widget_key=${encodeURIComponent(widgetKey)}&url=${encodeURIComponent(pageUrl)}`
  return request<Tour[]>(base, `/api/widget/tours${q}`, { headers: widgetHeaders(token) })
}

/**
 * GET ONE live tour by id, whatever its trigger type. This is what backs
 * `Stept('startTour', id)`: manual-trigger tours are excluded from the
 * eligible list by design, so resolving them from that list could never work.
 */
export function fetchTour(
  base: string,
  widgetKey: string,
  tourId: string,
  token?: string | null,
): Promise<Tour> {
  const q = `?widget_key=${encodeURIComponent(widgetKey)}`
  return request<Tour>(base, `/api/widget/tours/${encodeURIComponent(tourId)}${q}`, {
    headers: widgetHeaders(token),
  })
}

/** GET a tour for the dashboard preview link (any status/trigger/frequency). */
export function fetchPreviewTour(
  base: string,
  tourId: string,
  previewToken: string,
): Promise<Tour> {
  const q = `?preview_token=${encodeURIComponent(previewToken)}`
  return request<Tour>(base, `/api/widget/tours/${encodeURIComponent(tourId)}${q}`)
}

/** POST a tour lifecycle event (started / step_viewed / completed / dismissed / step_error). */
export async function postTourEvent(
  base: string,
  widgetKey: string,
  tourId: string,
  event: TourEventName,
  stepIndex: number | null,
  token?: string | null,
  meta?: TourEventMeta | null,
): Promise<void> {
  const q = `?widget_key=${encodeURIComponent(widgetKey)}`
  await request(base, `/api/widget/tours/${tourId}/events${q}`, {
    method: 'POST',
    headers: widgetHeaders(token),
    body: JSON.stringify({ event, step_index: stepIndex, meta: meta ?? null }),
  })
}

/**
 * POST one checklist item's progress. The server answers `{stored:false}` for
 * anonymous visitors (it keeps nothing for them) — the widget then persists the
 * state locally instead.
 */
export function postChecklistProgress(
  base: string,
  widgetKey: string,
  checklistId: string,
  itemId: string,
  done: boolean,
  token?: string | null,
): Promise<ChecklistProgressAck> {
  const q = `?widget_key=${encodeURIComponent(widgetKey)}`
  return request<ChecklistProgressAck>(
    base,
    `/api/widget/checklists/${encodeURIComponent(checklistId)}/progress${q}`,
    { method: 'POST', headers: widgetHeaders(token), body: JSON.stringify({ item_id: itemId, done }) },
  )
}

/** POST the checklist dismissal (identified visitors only; anonymous is local). */
export function postChecklistDismiss(
  base: string,
  widgetKey: string,
  checklistId: string,
  token?: string | null,
): Promise<{ ok: boolean; stored: boolean }> {
  const q = `?widget_key=${encodeURIComponent(widgetKey)}`
  return request<{ ok: boolean; stored: boolean }>(
    base,
    `/api/widget/checklists/${encodeURIComponent(checklistId)}/dismiss${q}`,
    { method: 'POST', headers: widgetHeaders(token) },
  )
}

/** POST survey answers. `completed:false` is a partial (dismissed) submission. */
export function postSurveyResponse(
  base: string,
  widgetKey: string,
  surveyId: string,
  answers: SurveyAnswer[],
  completed: boolean,
  token?: string | null,
  pageUrl?: string,
): Promise<SurveyAck> {
  const q =
    `?widget_key=${encodeURIComponent(widgetKey)}` +
    (pageUrl ? `&url=${encodeURIComponent(pageUrl)}` : '')
  return request<SurveyAck>(
    base,
    `/api/widget/surveys/${encodeURIComponent(surveyId)}/responses${q}`,
    { method: 'POST', headers: widgetHeaders(token), body: JSON.stringify({ answers, completed }) },
  )
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

/** The `error.code` carried by the API's error envelope, when there is one. */
export function errorCode(error: ApiError): string | null {
  const body = error.body as { error?: { code?: unknown } } | null
  const code = body?.error?.code
  return typeof code === 'string' ? code : null
}
