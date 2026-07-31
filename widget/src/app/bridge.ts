/** App-side half of the postMessage bridge to the host-page loader. */

import { envelope, type MessageType, parseEnvelope, type Envelope } from '../protocol'

export const bridge = {
  /** Send a message up to the loader (parent window). */
  post(type: MessageType, payload: unknown): void {
    try {
      window.parent?.postMessage(envelope(type, payload), '*')
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
