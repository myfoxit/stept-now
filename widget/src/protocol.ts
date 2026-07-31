/**
 * postMessage protocol shared by the host-page loader and the iframe app.
 *
 * Both bundles import these constants so the wire contract stays in one place.
 * Every message is wrapped in an envelope tagged with {@link ENVELOPE_SOURCE} so
 * we can safely ignore unrelated `message` events on the host `window`.
 */

export const ENVELOPE_SOURCE = 'stept-widget' as const

export const MSG = {
  /** app -> loader: iframe app mounted and finished booting. */
  READY: 'stept:ready',
  /** app -> loader: request a new iframe height (mobile / dynamic content). */
  RESIZE: 'stept:resize',
  /** app -> loader: unread count changed (drives the launcher badge). */
  UNREAD: 'stept:unread',
  /** both ways: open the messenger panel. */
  OPEN: 'stept:open',
  /** both ways: close the messenger panel. */
  CLOSE: 'stept:close',
  /** app -> loader: start a product tour in the host DOM. */
  TOUR_START: 'stept:tour:start',
  /** loader -> app: a tour lifecycle event happened (mirrors backend telemetry). */
  TOUR_EVENT: 'stept:tour:event',
  /**
   * loader -> app: an ongoing campaign's trigger rules matched on the host page
   * (payload `{campaignId}`). The app holds the visitor token, so it performs
   * the actual trigger POST.
   */
  CAMPAIGN_DUE: 'stept:campaign:due',
} as const

export type MessageType = (typeof MSG)[keyof typeof MSG]

export interface Envelope<T = unknown> {
  source: typeof ENVELOPE_SOURCE
  type: MessageType
  payload: T
}

/** Wrap a payload in the tagged envelope used across the postMessage bridge. */
export function envelope<T>(type: MessageType, payload: T): Envelope<T> {
  return { source: ENVELOPE_SOURCE, type, payload }
}

/** Narrow an arbitrary `MessageEvent.data` to one of our envelopes. */
export function parseEnvelope(data: unknown): Envelope | null {
  if (
    data &&
    typeof data === 'object' &&
    (data as Envelope).source === ENVELOPE_SOURCE &&
    typeof (data as Envelope).type === 'string'
  ) {
    return data as Envelope
  }
  return null
}
