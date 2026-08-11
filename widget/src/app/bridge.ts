/** App-side half of the postMessage bridge to the host-page loader. */

import { envelope, type MessageType, parseEnvelope, type Envelope } from '../protocol'
import type { ExtraMessageType } from './api-extra'

export const bridge = {
  /** Send a message up to the loader (parent window).
   *
   * Accepts the app-local {@link ExtraMessageType} names too — the cast below
   * disappears once the loader slice merges them into `protocol.ts` MSG. */
  post(type: MessageType | ExtraMessageType, payload: unknown): void {
    try {
      window.parent?.postMessage(envelope(type as MessageType, payload), '*')
    } catch {
      /* not embedded (e.g. opened standalone) — no-op */
    }
  },

  /** Listen for messages from the loader; returns an unsubscribe fn. */
  on(handler: (env: Envelope) => void): () => void {
    const listener = (event: MessageEvent): void => {
      const env = parseEnvelope(event.data)
      if (env) handler(env)
    }
    window.addEventListener('message', listener)
    return () => window.removeEventListener('message', listener)
  },
}
