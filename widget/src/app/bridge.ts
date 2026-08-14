/** App-side half of the postMessage bridge to the host-page loader. */

import { envelope, type MessageType, parseEnvelope, type Envelope } from '../protocol'
import type { ExtraMessageType } from './api-extra'

/**
 * The embedding page's origin, from the loader's frame-hash boot params
 * (`parentOrigin`). When set, inbound messages from any other origin — or from
 * any window that is not our parent — are dropped, and every post targets
 * exactly that origin. When absent (an older loader.js still cached on the
 * host page), both directions keep the legacy wildcard behaviour so a
 * mixed-version deployment keeps working.
 */
let parentOrigin: string | null = null

export const bridge = {
  /** Pin the bridge to the loader page's origin (null/undefined unpins). */
  setParentOrigin(origin: string | null | undefined): void {
    parentOrigin = origin || null
  },

  /** Send a message up to the loader (parent window).
   *
   * Accepts the app-local {@link ExtraMessageType} names too — the cast below
   * disappears once the loader slice merges them into `protocol.ts` MSG. */
  post(type: MessageType | ExtraMessageType, payload: unknown): void {
    try {
      window.parent?.postMessage(envelope(type as MessageType, payload), parentOrigin ?? '*')
    } catch {
      /* not embedded (e.g. opened standalone) — no-op */
    }
  },

  /** Listen for messages from the loader; returns an unsubscribe fn. */
  on(handler: (env: Envelope) => void): () => void {
    const listener = (event: MessageEvent): void => {
      // Pinned: only our embedding page may talk to us — anything else (another
      // frame, an opened window, a script with a reference to this one) is
      // ignored before its payload is even parsed.
      if (parentOrigin && (event.origin !== parentOrigin || event.source !== window.parent)) {
        return
      }
      const env = parseEnvelope(event.data)
      if (env) handler(env)
    }
    window.addEventListener('message', listener)
    return () => window.removeEventListener('message', listener)
  },
}
