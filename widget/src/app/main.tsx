import { render } from 'preact'

import type { Identity } from '../types'
import { App } from './App'
import { Controller, type BootParams } from './controller'
import './styles.css'

/**
 * Read boot params passed by the loader in the iframe URL hash
 * (`app.html#<encoded JSON>` — see `WidgetHost.frameSrc()`). Falls back to query
 * params so the app can also be opened standalone for manual testing
 * (`app.html?key=wk_…&apiBase=…&locale=de`).
 */
export function readParams(): BootParams {
  let parsed: {
    workspaceKey?: string
    widgetKey?: string
    apiBase?: string
    identity?: Identity
    locale?: string
    lockLocale?: boolean
    parentOrigin?: string
  } = {}
  const raw = location.hash.replace(/^#/, '')
  if (raw) {
    try {
      parsed = JSON.parse(decodeURIComponent(raw))
    } catch {
      /* ignore malformed hash */
    }
  }
  const query = new URLSearchParams(location.search)
  const workspaceKey =
    parsed.workspaceKey || parsed.widgetKey || query.get('key') || query.get('workspaceKey') || ''
  const apiBase = parsed.apiBase || query.get('apiBase') || location.origin
  const locale =
    (typeof parsed.locale === 'string' && parsed.locale) || query.get('locale') || undefined
  return {
    workspaceKey,
    apiBase,
    identity: parsed.identity,
    locale,
    // Truthy, mirroring the loader half, which feeds the host page's setting
    // into resolveLocale as-is.
    lockLocale: parsed.lockLocale ? true : undefined,
    parentOrigin:
      (typeof parsed.parentOrigin === 'string' && parsed.parentOrigin) || undefined,
  }
}

const root = document.getElementById('stept-root')
if (root) {
  const controller = new Controller(readParams())
  render(<App controller={controller} />, root)
}
