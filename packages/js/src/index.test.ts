// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'

import {
  loadStept,
  openStept,
  registerAction,
  removeAction,
  stept,
} from './index'

interface TestWindow {
  Stept?: ((...args: unknown[]) => void) & { q?: unknown[][] }
  SteptSettings?: unknown
}

const w = window as unknown as TestWindow

afterEach(() => {
  delete w.Stept
  delete w.SteptSettings
  document.getElementById('stept-loader-script')?.remove()
})

describe('stept queue shim', () => {
  it('queues calls made before the loader arrives, in order', () => {
    stept('boot', { workspaceKey: 'wk' })
    openStept()
    expect(w.Stept?.q).toEqual([['boot', { workspaceKey: 'wk' }], ['open']])
  })

  it('calls straight through once the real dispatcher is installed', () => {
    const calls: unknown[][] = []
    w.Stept = (...args: unknown[]) => calls.push(args)
    stept('close')
    expect(calls).toEqual([['close']])
  })
})

describe('registerAction / removeAction', () => {
  it('are plain commands, so they queue before load like everything else', () => {
    const def = { name: 'invite', description: 'x', run: () => 'ok' }
    registerAction(def)
    removeAction('invite')
    expect(w.Stept?.q).toEqual([
      ['action', def],
      ['removeAction', 'invite'],
    ])
  })
})

describe('loadStept', () => {
  it('stores settings and injects the loader script once', () => {
    const settings = { workspaceKey: 'wk_1', apiBase: 'https://stept.acme.io/' }
    loadStept(settings)
    loadStept(settings)
    const scripts = document.querySelectorAll('#stept-loader-script')
    expect(scripts).toHaveLength(1)
    expect((scripts[0] as HTMLScriptElement).src).toBe(
      'https://stept.acme.io/widget-assets/loader.js',
    )
    expect(w.SteptSettings).toEqual(settings)
  })

  it('re-boots instead of double-injecting when called again', () => {
    loadStept({ workspaceKey: 'wk_1', apiBase: 'https://stept.acme.io' })
    const second = { workspaceKey: 'wk_1', apiBase: 'https://stept.acme.io', identity: undefined }
    loadStept(second)
    // The second call queued a boot with the fresh settings.
    expect(w.Stept?.q?.some((call) => call[0] === 'boot')).toBe(true)
  })
})
