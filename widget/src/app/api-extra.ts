/**
 * App-local extensions to the wire contracts, pending the merge with the
 * loader/protocol slice.
 *
 * Everything here reads optional fields other slices are adding server-side
 * (boot config brand/agent identity, conversation subjects, tour_offer /
 * tour_event message attachments) and the two tour postMessages that do not
 * exist in `protocol.ts` yet. Every reader tolerates absence — the widget must
 * keep working against a backend or loader that has not shipped its half.
 */

import type {
  BootWorkspace,
  ConversationSummary,
  WidgetConfig,
  WidgetMessage,
} from '../types'

// --- postMessage names ------------------------------------------------------

// protocol: to be merged into protocol.ts MSG by the loader slice. Wire names
// follow the existing `stept:` envelope convention (MSG.TOUR_START is already
// 'stept:tour:start' with payload {tourId}).
export const MSG_EXTRA = {
  /** app -> loader: resume an interrupted tour (payload `{tourId}`). */
  TOUR_RESUME: 'stept:tour:resume',
  /** loader -> app: live tour progress (payload `{status,tourId,step,total,title}`). */
  TOUR_STATE: 'stept:tour:state',
} as const

export type ExtraMessageType = (typeof MSG_EXTRA)[keyof typeof MSG_EXTRA]

// --- boot config identity ---------------------------------------------------

/** What the end customer should see as the messenger's brand — never the
 * internal workspace name unless nothing better exists. */
export function brandDisplayName(
  config: WidgetConfig,
  workspace: BootWorkspace | null,
): string | null {
  const v = config.brand_display_name
  if (typeof v === 'string' && v.trim()) return v.trim()
  return workspace?.name || null
}

/** The AI agent's public persona ("Northplane Guide"), when configured. */
export function agentDisplayName(config: WidgetConfig): string | null {
  const v = config.agent_display_name
  return typeof v === 'string' && v.trim() ? v.trim() : null
}

/** Whether agent messages must carry the "AI" disclosure chip. Default ON —
 * disclosure is opt-out, absence of the flag must never hide it. */
export function aiDisclosureEnabled(config: WidgetConfig): boolean {
  return config.ai_disclosure !== false
}

// --- conversation summaries -------------------------------------------------

/** Optional fields the backend slice starts generating. */
interface ConversationSummaryExtras {
  subject?: string | null
  title?: string | null
}

/** Backend-generated subject/title for a conversation row, when present. */
export function conversationSubject(c: ConversationSummary): string | null {
  const extra = c as ConversationSummary & ConversationSummaryExtras
  for (const v of [extra.subject, extra.title]) {
    if (typeof v === 'string' && v.trim()) return v.trim()
  }
  return null
}

/**
 * "Your turn": the agent asked the visitor something and is waiting.
 *
 * Heuristic on purpose — the summary carries no last-message direction, so we
 * treat an unread conversation whose preview ends in a question mark (incl. the
 * fullwidth ？ used by ja/zh and the inverted Arabic ؟) as the agent waiting on
 * an answer.
 */
export function isYourTurn(c: ConversationSummary): boolean {
  if (!c.unread) return false
  return /[?？؟]\s*$/.test((c.last_message_preview || '').trim())
}

// --- message attachments ----------------------------------------------------

/** `{"kind":"tour_offer",...}` on an agent message → rendered as a TourCard. */
export interface TourOfferAttachment {
  kind: 'tour_offer'
  tour_id: string
  title: string
  steps: number
  est_seconds: number
}

/** First tour_offer attachment on a message, or null. */
export function tourOffer(message: WidgetMessage): TourOfferAttachment | null {
  for (const a of message.attachments || []) {
    if (a && a.kind === 'tour_offer' && typeof a.tour_id === 'string') {
      return {
        kind: 'tour_offer',
        tour_id: a.tour_id,
        title: typeof a.title === 'string' ? a.title : '',
        steps: typeof a.steps === 'number' ? a.steps : 0,
        est_seconds: typeof a.est_seconds === 'number' ? a.est_seconds : 0,
      }
    }
  }
  return null
}

/** `{"kind":"tour_event",...}`: lifecycle telemetry echoed into the thread. */
export interface TourEventAttachment {
  kind: 'tour_event'
  event?: string
  title?: string
  step?: number
}

/** First tour_event attachment on a message, or null. Such messages render as
 * subtle centered system lines, never as chat bubbles, and must never become a
 * conversation preview or an unread signal. */
export function tourEvent(message: WidgetMessage): TourEventAttachment | null {
  for (const a of message.attachments || []) {
    if (a && a.kind === 'tour_event') {
      return {
        kind: 'tour_event',
        event: typeof a.event === 'string' ? a.event : undefined,
        title: typeof a.title === 'string' ? a.title : undefined,
        step: typeof a.step === 'number' ? a.step : undefined,
      }
    }
  }
  return null
}

// --- live tour state (loader → app) ----------------------------------------

export interface TourState {
  status: string
  tourId: string
  step: number | null
  total: number | null
  title: string
}

/** Parse a `tour:state` payload; tolerates the legacy TOUR_EVENT shape
 * (`{tourId, event, stepIndex}`) the current loader still sends. */
export function parseTourState(payload: Record<string, unknown>): TourState | null {
  const status =
    typeof payload.status === 'string'
      ? payload.status
      : typeof payload.event === 'string'
        ? payload.event
        : ''
  if (!status) return null
  const tourId =
    typeof payload.tourId === 'string'
      ? payload.tourId
      : typeof payload.tour_id === 'string'
        ? payload.tour_id
        : ''
  const step =
    typeof payload.step === 'number'
      ? payload.step
      : typeof payload.stepIndex === 'number'
        ? payload.stepIndex + 1 // stepIndex is 0-based; step is human 1-based
        : null
  return {
    status,
    tourId,
    step,
    total: typeof payload.total === 'number' ? payload.total : null,
    title: typeof payload.title === 'string' ? payload.title : '',
  }
}

// --- conversation starters --------------------------------------------------

/** A suggested-question chip for an empty conversation. Clicking one sends
 * `text` verbatim as the visitor's message — the chip never says one thing and
 * sends another. */
export interface Starter {
  kind: 'tour' | 'article'
  text: string
}

// --- federated search -------------------------------------------------------

/** One grouped result set for the Home search: articles + live tours. */
export interface FederatedResults {
  query: string
  loading: boolean
  articles: Array<{ title: string; slug: string; snippet: string }>
  tours: Array<{ id: string; name: string; steps: number }>
}
