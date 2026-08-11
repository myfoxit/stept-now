import { render } from 'preact'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { MSG } from '../protocol'
import { App } from './App'
import { Controller } from './controller'

class FakeWebSocket {
  static OPEN = 1
  static CONNECTING = 0
  readyState = 0
  close(): void {}
  send(): void {}
}

const bootBody = {
  token: 'tok-1',
  visitor_id: 'v1',
  contact: { id: 'ct1', name: 'Visitor', email: null },
  // The workspace's internal name is "Stept" — the end customer must never
  // see it once a brand display name is configured.
  workspace: { name: 'Stept', logo_url: null },
  config: { brand_display_name: 'Northplane', agent_display_name: 'Northplane Guide' },
  conversations: [],
  help_center_enabled: false,
}

let container: HTMLDivElement | null = null
let controller: Controller | null = null

afterEach(() => {
  if (container) {
    render(null, container)
    container.remove()
    container = null
  }
  controller?.dispose()
  controller = null
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

async function mountApp(): Promise<HTMLDivElement> {
  vi.stubGlobal('WebSocket', FakeWebSocket)
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({
      ok: true,
      status: 200,
      statusText: 'stub',
      text: async () => JSON.stringify(bootBody),
    })),
  )
  controller = new Controller({ workspaceKey: 'wk_t', apiBase: 'http://api:8600' })
  container = document.createElement('div')
  document.body.appendChild(container)
  render(<App controller={controller} />, container)
  await vi.waitFor(() => {
    expect(container!.textContent).toContain('Northplane')
  })
  return container
}

describe('App', () => {
  it('titles the header with the brand display name, not the workspace name', async () => {
    const el = await mountApp()
    const title = el.querySelector('.sw-header-title')
    expect(title!.textContent).toBe('Northplane')
    expect(el.querySelector('.sw-header')!.textContent).not.toContain('Stept')
  })

  it('closes the panel on Escape via the loader CLOSE message', async () => {
    const posted: Array<{ type?: string }> = []
    vi.spyOn(window, 'postMessage').mockImplementation((data: unknown) => {
      posted.push(data as { type?: string })
    })
    await mountApp()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    expect(posted.some((p) => p?.type === MSG.CLOSE)).toBe(true)
  })
})
