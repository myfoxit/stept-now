/**
 * readParams — the app's half of the frame-hash contract with
 * `WidgetHost.frameSrc()`. Everything the loader knows at boot travels in one
 * encoded-JSON hash; query params exist only for opening app.html standalone.
 */

import { afterEach, describe, expect, it } from 'vitest'

import { readParams } from './main'

function setHash(params: Record<string, unknown>): void {
  location.hash = `#${encodeURIComponent(JSON.stringify(params))}`
}

afterEach(() => {
  location.hash = ''
})

describe('readParams', () => {
  it('parses locale, lockLocale and parentOrigin from the loader hash', () => {
    setHash({
      workspaceKey: 'wk_1',
      apiBase: 'http://api.test',
      locale: 'de',
      lockLocale: true,
      parentOrigin: 'https://host.example',
    })
    expect(readParams()).toEqual({
      workspaceKey: 'wk_1',
      apiBase: 'http://api.test',
      identity: undefined,
      locale: 'de',
      lockLocale: true,
      parentOrigin: 'https://host.example',
    })
  })

  it('leaves them unset when the loader sent none (older loader, no settings)', () => {
    setHash({ workspaceKey: 'wk_1', apiBase: 'http://api.test' })
    const params = readParams()
    expect(params.workspaceKey).toBe('wk_1')
    expect(params.locale).toBeUndefined()
    expect(params.lockLocale).toBeUndefined()
    expect(params.parentOrigin).toBeUndefined()
  })

  it('accepts a locale query param for standalone manual testing', () => {
    // No hash — the standalone `app.html?key=…&locale=…` path.
    const url = new URL(location.href)
    url.search = '?key=wk_q&locale=fr'
    url.hash = ''
    history.replaceState(null, '', url)
    try {
      const params = readParams()
      expect(params.workspaceKey).toBe('wk_q')
      expect(params.locale).toBe('fr')
    } finally {
      history.replaceState(null, '', `${location.pathname}`)
    }
  })
})
