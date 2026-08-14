/**
 * App-side bridge origin pinning. When the loader passes its page origin in
 * the frame-hash boot params, the listener must drop anything that is not from
 * that origin AND our parent window, and every post must target it. When no
 * origin arrived — an older loader.js still cached on the host page — both
 * directions keep the legacy wildcard behaviour, so a mixed-version deployment
 * (the loader cache in prod is 5 minutes) keeps working.
 */

import { afterEach, describe, expect, it, vi } from 'vitest'

import { envelope, MSG, type Envelope } from '../protocol'
import { bridge } from './bridge'

const HOST = 'https://host.example'

/** Bind a handler, dispatch one message event, unbind; return what got through. */
function deliver(init: MessageEventInit): Envelope[] {
  const seen: Envelope[] = []
  const off = bridge.on((env) => seen.push(env))
  window.dispatchEvent(new MessageEvent('message', init))
  off()
  return seen
}

afterEach(() => {
  bridge.setParentOrigin(null)
  vi.restoreAllMocks()
})

describe('bridge origin pinning', () => {
  it('drops a message from a wrong origin when pinned', () => {
    bridge.setParentOrigin(HOST)
    // source IS the parent — only the origin is wrong.
    const seen = deliver({
      data: envelope(MSG.OPEN, {}),
      origin: 'https://evil.example',
      source: window,
    })
    expect(seen).toEqual([])
  })

  it('drops a message whose source is not the parent window when pinned', () => {
    bridge.setParentOrigin(HOST)
    // origin matches, but source (null here) is not window.parent.
    const seen = deliver({ data: envelope(MSG.OPEN, {}), origin: HOST })
    expect(seen).toEqual([])
  })

  it('accepts a message from the pinned origin and parent window', () => {
    bridge.setParentOrigin(HOST)
    // Under jsdom window.parent === window, so `source: window` is the parent.
    const seen = deliver({ data: envelope(MSG.OPEN, {}), origin: HOST, source: window })
    expect(seen).toHaveLength(1)
    expect(seen[0]!.type).toBe(MSG.OPEN)
  })

  it('posts to the pinned origin instead of *', () => {
    bridge.setParentOrigin(HOST)
    const spy = vi.spyOn(window, 'postMessage').mockImplementation(() => {})
    bridge.post(MSG.UNREAD, { count: 2 })
    expect(spy).toHaveBeenCalledWith(envelope(MSG.UNREAD, { count: 2 }), HOST)
  })

  it('keeps the legacy wildcard behaviour when no parentOrigin arrived', () => {
    const seen = deliver({ data: envelope(MSG.OPEN, {}), origin: 'https://any.example' })
    expect(seen).toHaveLength(1)
    const spy = vi.spyOn(window, 'postMessage').mockImplementation(() => {})
    bridge.post(MSG.CLOSE, {})
    expect(spy).toHaveBeenCalledWith(envelope(MSG.CLOSE, {}), '*')
  })
})
