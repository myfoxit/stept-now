/**
 * The host-page agent runtime: what the AI can see, what it can do, and — the
 * part that matters most — what it is refused.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

import { isOffLimits, PageAgent, waitForDomSettle } from './page-agent'

/** No settle wait in tests: jsdom has no renderer to wait for. */
function agent(overrides: Partial<ConstructorParameters<typeof PageAgent>[0]> = {}): PageAgent {
  return new PageAgent({ settleMs: 0, ...overrides })
}

function mount(html: string): void {
  document.body.innerHTML = html
  for (const el of document.querySelectorAll('*')) {
    el.getBoundingClientRect = () =>
      ({ top: 10, left: 10, bottom: 40, right: 120, width: 110, height: 30, x: 10, y: 10 }) as DOMRect
  }
}

beforeEach(() => {
  document.body.innerHTML = ''
  document.title = 'Billing — Acme'
})

describe('snapshot', () => {
  it('returns the numbered listing plus where the visitor is', async () => {
    mount('<button>Create invoice</button>')
    const result = await agent().run({ op: 'snapshot' })
    expect(result.ok).toBe(true)
    expect(result.elements).toContain('Create invoice')
    expect(result.title).toBe('Billing — Acme')
    expect(result.url).toBe(document.location.href)
  })

  it('pages a long listing from an offset', async () => {
    mount(Array.from({ length: 40 }, (_, i) => `<button>Row ${i}</button>`).join(''))
    const result = await agent({ maxElementChars: 120 }).run({ op: 'snapshot' })
    expect(result.note).toBeUndefined()
    expect(result.elements).toContain('offset=')
    const paged = await agent({ maxElementChars: 120 }).run({ op: 'snapshot', args: { offset: 5 } })
    expect(paged.elements?.split('\n')[0]).toContain('[5]')
  })

  it('hides the widget’s own UI so the AI cannot click itself', async () => {
    mount('<div id="stept-frame"><button>Send</button></div><button>Real button</button>')
    const result = await agent().run({ op: 'snapshot' })
    expect(result.elements).toContain('Real button')
    expect(result.elements).not.toContain('Send')
  })

  it('respects a host page opt-out fence', async () => {
    mount('<div data-stept-no-ai><button>Delete account</button></div><button>Safe</button>')
    const result = await agent().run({ op: 'snapshot' })
    expect(result.elements).not.toContain('Delete account')
    expect(result.elements).toContain('Safe')
  })
})

describe('find', () => {
  it('locates an element by its text and returns its index', async () => {
    mount('<button>Cancel</button><button>Create invoice</button>')
    const result = await agent().run({ op: 'find', args: { query: 'invoice' } })
    expect(result.found?.[0]?.name).toBe('Create invoice')
    expect(result.found?.[0]?.index).toBe(1)
  })

  it('says nothing matched instead of returning an empty success', async () => {
    mount('<button>Cancel</button>')
    const result = await agent().run({ op: 'find', args: { query: 'invoice' } })
    expect(result.found).toEqual([])
    expect(result.note).toContain('nothing matching')
  })

  it('rejects a blank query', async () => {
    const result = await agent().run({ op: 'find', args: { query: '  ' } })
    expect(result.ok).toBe(false)
    expect(result.error).toContain('needs a query')
  })
})

describe('read', () => {
  it('returns page prose without the widget’s own chrome', async () => {
    mount('<div id="stept-frame"><p>Chat with us</p></div><p>Invoice #42 is overdue</p>')
    const result = await agent().run({ op: 'read' })
    expect(result.text).toContain('Invoice #42 is overdue')
    expect(result.text).not.toContain('Chat with us')
  })

  it('honours a char cap', async () => {
    mount(`<p>${'a'.repeat(5000)}</p>`)
    const result = await agent().run({ op: 'read', args: { max_chars: 300 } })
    expect(result.text).toHaveLength(300)
  })
})

describe('act', () => {
  it('clicks the element at an index', async () => {
    mount('<button id="go">Create invoice</button>')
    const clicked = vi.fn()
    document.getElementById('go')!.addEventListener('click', clicked)
    const runner = agent()
    await runner.run({ op: 'snapshot' })
    const result = await runner.run({ op: 'act', args: { index: 0, kind: 'click' } })
    expect(clicked).toHaveBeenCalledOnce()
    expect(result.ok).toBe(true)
  })

  it('fills an input through the native setter so frameworks see the change', async () => {
    mount('<input id="amount" aria-label="Amount">')
    const events: string[] = []
    const input = document.getElementById('amount') as HTMLInputElement
    input.addEventListener('input', () => events.push('input'))
    input.addEventListener('change', () => events.push('change'))
    const runner = agent()
    await runner.run({ op: 'snapshot' })
    await runner.run({ op: 'act', args: { index: 0, kind: 'fill', text: '250' } })
    expect(input.value).toBe('250')
    expect(events).toEqual(['input', 'change'])
  })

  it('refuses to type into a password field', async () => {
    mount('<input id="pw" type="password" aria-label="Password">')
    const runner = agent()
    await runner.run({ op: 'snapshot' })
    const result = await runner.run({ op: 'act', args: { index: 0, kind: 'fill', text: 'hunter2' } })
    expect(result.ok).toBe(false)
    expect(result.error).toContain('password')
    expect((document.getElementById('pw') as HTMLInputElement).value).toBe('')
  })

  it('selects an option by its label', async () => {
    mount('<select aria-label="Plan"><option value="f">Free</option><option value="p">Pro</option></select>')
    const runner = agent()
    await runner.run({ op: 'snapshot' })
    const result = await runner.run({ op: 'act', args: { index: 0, kind: 'select', text: 'Pro' } })
    expect(result.ok).toBe(true)
    expect((document.querySelector('select') as HTMLSelectElement).value).toBe('p')
  })

  it('reports an unmatched option instead of silently picking the first', async () => {
    mount('<select aria-label="Plan"><option>Free</option></select>')
    const runner = agent()
    await runner.run({ op: 'snapshot' })
    const result = await runner.run({ op: 'act', args: { index: 0, kind: 'select', text: 'Enterprise' } })
    expect(result.ok).toBe(false)
    expect(result.error).toContain('no option matching')
  })

  it('checks a checkbox only when it is not already checked', async () => {
    mount('<input type="checkbox" aria-label="Terms" checked>')
    const box = document.querySelector('input') as HTMLInputElement
    const runner = agent()
    await runner.run({ op: 'snapshot' })
    await runner.run({ op: 'act', args: { index: 0, kind: 'check' } })
    expect(box.checked).toBe(true)
    await runner.run({ op: 'act', args: { index: 0, kind: 'uncheck' } })
    expect(box.checked).toBe(false)
  })

  it('needs an index', async () => {
    mount('<button>Go</button>')
    const result = await agent().run({ op: 'act', args: { kind: 'click' } })
    expect(result.ok).toBe(false)
    expect(result.error).toContain('needs the element index')
  })

  it('tells the model to re-snapshot when the index is gone', async () => {
    mount('<button>Go</button>')
    const runner = agent()
    await runner.run({ op: 'snapshot' })
    document.body.innerHTML = '<p>Different page</p>'
    const result = await runner.run({ op: 'act', args: { index: 0, kind: 'click' } })
    expect(result.ok).toBe(false)
    expect(result.error).toContain('fresh page_snapshot')
  })

  it('re-finds an element whose index stamp an SPA re-render wiped', async () => {
    mount('<button id="go">Go</button>')
    const clicked = vi.fn()
    document.getElementById('go')!.addEventListener('click', clicked)
    const runner = agent()
    await runner.run({ op: 'snapshot' })
    document.getElementById('go')!.removeAttribute('data-stept-idx')
    const result = await runner.run({ op: 'act', args: { index: 0, kind: 'click' } })
    expect(result.ok).toBe(true)
    expect(clicked).toHaveBeenCalledOnce()
  })

  it('warns when a click changed nothing visible', async () => {
    mount('<button>Inert</button>')
    const runner = agent()
    await runner.run({ op: 'snapshot' })
    const result = await runner.run({ op: 'act', args: { index: 0, kind: 'click' } })
    expect(result.note).toContain('no visible change')
  })

  it('does not warn when the click did change the page', async () => {
    mount('<button id="go">Add row</button>')
    document.getElementById('go')!.addEventListener('click', () => {
      const extra = document.createElement('button')
      extra.textContent = 'Remove row'
      extra.getBoundingClientRect = () =>
        ({ top: 10, left: 10, bottom: 40, right: 120, width: 110, height: 30, x: 10, y: 10 }) as DOMRect
      document.body.appendChild(extra)
    })
    const runner = agent()
    await runner.run({ op: 'snapshot' })
    const result = await runner.run({ op: 'act', args: { index: 0, kind: 'click' } })
    expect(result.note ?? '').not.toContain('no visible change')
    expect(result.elements).toContain('Remove row')
  })

  it('refuses to act on the widget’s own DOM even if addressed directly', async () => {
    mount('<div id="stept-frame"><button data-stept-idx="0">Send</button></div>')
    const result = await agent().run({ op: 'act', args: { index: 0, kind: 'click' } })
    expect(result.ok).toBe(false)
    expect(result.error).toContain('not available')
  })

  it('submits the surrounding form', async () => {
    mount('<form id="f"><input aria-label="Email"><button type="submit">Save</button></form>')
    const form = document.getElementById('f') as HTMLFormElement
    const submitted = vi.fn()
    form.addEventListener('submit', (event) => {
      event.preventDefault()
      submitted()
    })
    form.requestSubmit = () => form.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }))
    const runner = agent()
    await runner.run({ op: 'snapshot' })
    const result = await runner.run({ op: 'act', args: { index: 0, kind: 'submit' } })
    expect(result.ok).toBe(true)
    expect(submitted).toHaveBeenCalledOnce()
  })
})

describe('navigate', () => {
  it('allows a same-origin path', async () => {
    const assign = vi.fn()
    const result = await agent({
      win: { location: { assign } } as unknown as Window & typeof globalThis,
    }).run({ op: 'navigate', args: { url: '/billing/invoices' } })
    expect(result.ok).toBe(true)
    expect(assign).toHaveBeenCalledWith(`${document.location.origin}/billing/invoices`)
  })

  it('refuses a cross-origin destination', async () => {
    const assign = vi.fn()
    const result = await agent({
      win: { location: { assign } } as unknown as Window & typeof globalThis,
    }).run({ op: 'navigate', args: { url: 'https://evil.example/steal' } })
    expect(result.ok).toBe(false)
    expect(result.error).toContain('not allowed')
    expect(assign).not.toHaveBeenCalled()
  })

  it('allows an origin the host explicitly opted in', async () => {
    const assign = vi.fn()
    const result = await agent({
      win: { location: { assign } } as unknown as Window & typeof globalThis,
      allowedOrigins: ['https://app.partner.example'],
    }).run({ op: 'navigate', args: { url: 'https://app.partner.example/import' } })
    expect(result.ok).toBe(true)
    expect(assign).toHaveBeenCalledOnce()
  })

  it('rejects a malformed url', async () => {
    const result = await agent().run({ op: 'navigate', args: { url: 'http://' } })
    expect(result.ok).toBe(false)
  })
})

describe('scroll and wait', () => {
  it('scrolls and answers with a fresh snapshot', async () => {
    mount('<button>Below</button>')
    const scrollBy = vi.fn()
    const result = await agent({
      win: { scrollBy, location: document.location } as unknown as Window & typeof globalThis,
    }).run({ op: 'scroll', args: { dir: 'down', amount: 400 } })
    expect(scrollBy).toHaveBeenCalledWith({ top: 400, behavior: 'instant' })
    expect(result.note).toContain('scrolled down 400px')
  })

  it('clamps a silly wait to the 8s ceiling', async () => {
    vi.useFakeTimers()
    try {
      const pending = agent().run({ op: 'wait', args: { ms: 999_999 } })
      await vi.advanceTimersByTimeAsync(8000)
      expect((await pending).note).toContain('waited 8000ms')
    } finally {
      vi.useRealTimers()
    }
  })

  it('rejects an unknown op rather than throwing', async () => {
    const result = await agent().run({ op: 'teleport' as never })
    expect(result.ok).toBe(false)
    expect(result.error).toContain('unknown op')
  })
})

describe('guardrail helpers', () => {
  it('marks widget-owned and fenced elements off limits', () => {
    mount(
      '<div id="stept-launcher"><span id="a"></span></div>' +
        '<div class="stept-tour-tip"><span id="b"></span></div>' +
        '<div data-stept-no-ai><span id="c"></span></div>' +
        '<span id="d"></span>',
    )
    expect(isOffLimits(document.getElementById('a')!)).toBe(true)
    expect(isOffLimits(document.getElementById('b')!)).toBe(true)
    expect(isOffLimits(document.getElementById('c')!)).toBe(true)
    expect(isOffLimits(document.getElementById('d')!)).toBe(false)
  })

  it('settles immediately on a quiet document', async () => {
    const started = Date.now()
    await waitForDomSettle(document, 10, 200)
    expect(Date.now() - started).toBeLessThan(200)
  })

  it('skips the wait entirely when quietMs is zero', async () => {
    await expect(waitForDomSettle(document, 0)).resolves.toBeUndefined()
  })
})
