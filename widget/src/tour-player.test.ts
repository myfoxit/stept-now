import { afterEach, describe, expect, it, vi } from 'vitest'

import type { Tour, TourEventMeta, TourEventName, TourStep } from './types'
import {
  absolutizeMedia,
  clearTourProgress,
  computeTooltipPosition,
  fillElement,
  performAction,
  readTourProgress,
  resolveAutoPlacement,
  resolveMediaUrl,
  selectFirstEligibleTour,
  TourPlayer,
  tourProgressKey,
  writeTourProgress,
} from './tour-player'

const vp = { width: 1000, height: 800 }
const tip = { width: 300, height: 140 }

describe('resolveAutoPlacement', () => {
  it('prefers below the target when there is room', () => {
    const target = { top: 100, left: 400, width: 120, height: 40 }
    expect(resolveAutoPlacement(target, tip, vp)).toBe('bottom')
  })

  it('flips above when the target hugs the bottom edge', () => {
    const target = { top: 760, left: 400, width: 120, height: 30 }
    expect(resolveAutoPlacement(target, tip, vp)).toBe('top')
  })
})

describe('computeTooltipPosition', () => {
  it('centers horizontally under the target for bottom placement', () => {
    const target = { top: 100, left: 400, width: 120, height: 40 }
    const pos = computeTooltipPosition('bottom', target, tip, vp)
    expect(pos.side).toBe('bottom')
    expect(pos.top).toBe(100 + 40 + 12)
    // center: 400 + 60 - 150 = 310
    expect(pos.left).toBe(310)
  })

  it('clamps within the viewport (never off-screen left)', () => {
    const target = { top: 100, left: 0, width: 20, height: 20 }
    const pos = computeTooltipPosition('left', target, tip, vp)
    expect(pos.left).toBeGreaterThanOrEqual(8)
    expect(pos.top).toBeGreaterThanOrEqual(8)
  })
})

describe('selectFirstEligibleTour', () => {
  const tours: Tour[] = [
    { id: 'a', name: 'A', steps: [{ id: 's1', selector: '#x', title: '', body: '', placement: 'auto' }], theme: { accent: '#000' }, version: 1 },
    { id: 'b', name: 'B', steps: [{ id: 's1', selector: '#y', title: '', body: '', placement: 'auto' }], theme: { accent: '#000' }, version: 1 },
  ]

  it('returns the first tour not already seen', () => {
    expect(selectFirstEligibleTour(tours, ['a'])?.id).toBe('b')
  })

  it('returns null when all tours are seen', () => {
    expect(selectFirstEligibleTour(tours, ['a', 'b'])).toBeNull()
  })

  it('skips tours with no steps', () => {
    const empty: Tour[] = [{ id: 'z', name: 'Z', steps: [], theme: { accent: '#000' }, version: 1 }]
    expect(selectFirstEligibleTour(empty, [])).toBeNull()
  })

  it('lets an every_time tour bypass the local seen-set', () => {
    const recurring: Tour[] = [{ ...tours[0]!, frequency_type: 'every_time' }]
    expect(selectFirstEligibleTour(recurring, ['a'])?.id).toBe('a')
  })
})

describe('resolveMediaUrl', () => {
  it('prefixes root-relative backend media with the API origin', () => {
    // The player renders on the CUSTOMER's domain, where /api/... would 404.
    expect(resolveMediaUrl('/api/widget/media/ws1/public/ws1/a.png', 'http://api.test:8600')).toBe(
      'http://api.test:8600/api/widget/media/ws1/public/ws1/a.png',
    )
    expect(resolveMediaUrl('media/a.png', 'http://api.test:8600/')).toBe(
      'http://api.test:8600/media/a.png',
    )
  })

  it('rewrites relative <img> sources in a rendered body, leaving CDN ones alone', () => {
    // Whatever the markdown renderer emits, the body is post-processed: the
    // player runs on the customer's origin, the media lives on the API's.
    const host = document.createElement('div')
    host.innerHTML =
      '<img src="/api/widget/media/ws1/public/ws1/s.png"><img src="https://cdn.test/x.png">'
    absolutizeMedia(host, 'http://api.test:8600')
    expect([...host.querySelectorAll('img')].map((i) => i.getAttribute('src'))).toEqual([
      'http://api.test:8600/api/widget/media/ws1/public/ws1/s.png',
      'https://cdn.test/x.png',
    ])
  })

  it('passes absolute and scheme-qualified URLs through untouched', () => {
    expect(resolveMediaUrl('https://cdn.example.com/a.png', 'http://api.test')).toBe(
      'https://cdn.example.com/a.png',
    )
    expect(resolveMediaUrl('//cdn.example.com/a.png', 'http://api.test')).toBe(
      '//cdn.example.com/a.png',
    )
    expect(resolveMediaUrl('data:image/png;base64,AAA', 'http://api.test')).toBe(
      'data:image/png;base64,AAA',
    )
    expect(resolveMediaUrl('', 'http://api.test')).toBe('')
  })
})

// --- player v2 ---------------------------------------------------------------

interface Recorded {
  event: TourEventName
  stepIndex: number | null
  meta?: TourEventMeta
}

function memoryStorage(): Storage {
  const map = new Map<string, string>()
  return {
    get length() {
      return map.size
    },
    clear: () => map.clear(),
    getItem: (k: string) => (map.has(k) ? map.get(k)! : null),
    key: (i: number) => [...map.keys()][i] ?? null,
    removeItem: (k: string) => void map.delete(k),
    setItem: (k: string, v: string) => void map.set(k, String(v)),
  }
}

function step(partial: Partial<TourStep> & { id: string }): TourStep {
  return { selector: '', title: '', body: '', placement: 'auto', ...partial }
}

function tourOf(steps: TourStep[], extra: Partial<Tour> = {}): Tour {
  return {
    id: 'tour-1',
    name: 'Tour',
    steps,
    theme: { accent: '#6366f1' },
    version: 1,
    ...extra,
  }
}

const flush = (ms = 0): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms))

let player: TourPlayer | null = null

function makePlayer(
  events: Recorded[],
  opts: Partial<ConstructorParameters<typeof TourPlayer>[0]> = {},
): TourPlayer {
  player = new TourPlayer({
    storage: memoryStorage(),
    progressKey: tourProgressKey('wk_test'),
    resolveTimeoutMs: 0,
    actionDelayMs: 0,
    onEvent: (event, stepIndex, meta) => events.push({ event, stepIndex, meta }),
    ...opts,
  })
  return player
}

const tipEl = (): HTMLElement | null => document.querySelector('.stept-tour-tip')
const tipText = (): string => tipEl()?.textContent ?? ''

afterEach(() => {
  player?.stop()
  player = null
  document.body.innerHTML = ''
  document.getElementById('stept-tour-style')?.remove()
  vi.useRealTimers()
})

describe('TourPlayer rendering', () => {
  it('renders a tooltip with markdown body + progress and reports started/step_viewed', () => {
    document.body.innerHTML = '<button id="save">Save</button>'
    const events: Recorded[] = []
    makePlayer(events).start(
      tourOf([
        step({ id: 's1', selector: '#save', title: 'Save it', body: 'Click **save** now' }),
        step({ id: 's2', selector: '#save', title: 'Second' }),
      ]),
    )
    expect(tipEl()).toBeTruthy()
    expect(tipEl()!.querySelector('.stept-tour-body')!.innerHTML).toContain('<strong>save</strong>')
    expect(tipText()).toContain('1 of 2')
    expect(events.map((e) => e.event)).toEqual(['started', 'step_viewed'])
    expect(events[0]!.stepIndex).toBeNull()
    expect(events[1]!.meta).toMatchObject({ viewport_w: expect.any(Number) })
    expect(String(events[1]!.meta!.url)).toContain('http')
  })

  it('flags a healed resolution on the step_viewed event', () => {
    document.body.innerHTML = '<button class="save">Save</button>'
    const events: Recorded[] = []
    makePlayer(events).start(
      tourOf([step({ id: 's1', selector: '#gone', fallback_selectors: ['.save'], title: 'X' })]),
    )
    expect(events.find((e) => e.event === 'step_viewed')!.meta!.healed).toBe(true)
  })

  it('renders a modal centred with aria-modal and no spotlight', () => {
    const events: Recorded[] = []
    makePlayer(events).start(tourOf([step({ id: 's1', type: 'modal', title: 'Welcome' })]))
    expect(tipEl()!.classList.contains('stept-centered')).toBe(true)
    expect(tipEl()!.getAttribute('aria-modal')).toBe('true')
    expect(tipEl()!.getAttribute('aria-labelledby')).toBe('stept-tour-title-0')
    expect(document.querySelector('.stept-tour-hole')!.hasAttribute('hidden')).toBe(true)
  })

  it('renders a banner bar that never blocks the page', () => {
    const events: Recorded[] = []
    makePlayer(events).start(
      tourOf([step({ id: 's1', type: 'banner', title: 'New!', body: 'Ship it' })], {
        kind: 'banner',
        theme: { accent: '#000', position: 'top' },
      }),
    )
    const banner = document.querySelector('.stept-tour-banner') as HTMLElement
    expect(banner.hidden).toBe(false)
    expect(banner.classList.contains('stept-top')).toBe(true)
    expect(tipEl()!.hidden).toBe(true)
    // The root veil (the only pointer-events:auto layer) must stay off.
    expect(document.querySelector('.stept-tour-root')!.classList.contains('stept-veil')).toBe(false)
  })

  it('keeps a pre-v2.1 banner looking exactly as it did', () => {
    // No `theme.banner` at all: full-width accent overlay, no icon, no extras.
    makePlayer([]).start(
      tourOf([step({ id: 's1', type: 'banner', title: 'New!' })], {
        kind: 'banner',
        theme: { accent: '#123456', position: 'bottom' },
      }),
    )
    const banner = document.querySelector('.stept-tour-banner') as HTMLElement
    expect(banner.classList.contains('stept-bottom')).toBe(true)
    expect(banner.classList.contains('stept-inline')).toBe(false)
    expect(banner.classList.contains('stept-boxed')).toBe(false)
    expect(banner.style.getPropertyValue('--stept-banner-bg')).toBe('#123456')
    expect(banner.style.width).toBe('')
    expect(banner.querySelector('.stept-tour-banner-icon')).toBeNull()
  })

  it('applies every banner presentation option', () => {
    makePlayer([]).start(
      tourOf([step({ id: 's1', type: 'banner', title: 'Maintenance' })], {
        kind: 'banner',
        theme: {
          accent: '#6366f1',
          position: 'top',
          banner: {
            layout: 'inline',
            full_width: false,
            max_width: 720,
            align: 'center',
            background: '#0f172a',
            text_color: '#f8fafc',
            icon: '🎉',
            dismiss: 'never_again',
            rounded: true,
          },
        },
      }),
    )
    const banner = document.querySelector('.stept-tour-banner') as HTMLElement
    expect(banner.classList.contains('stept-inline')).toBe(true)
    expect(banner.classList.contains('stept-boxed')).toBe(true)
    expect(banner.classList.contains('stept-center')).toBe(true)
    expect(banner.style.width).toBe('720px')
    expect(banner.style.getPropertyValue('--stept-banner-bg')).toBe('#0f172a')
    expect(banner.style.getPropertyValue('--stept-banner-fg')).toBe('#f8fafc')
    expect(banner.querySelector('.stept-tour-banner-icon')!.textContent).toBe('🎉')
    // Decorative: a screen reader should hear the title, not "party popper".
    expect(banner.querySelector('.stept-tour-banner-icon')!.getAttribute('aria-hidden')).toBe('true')
  })

  it('puts an inline banner into the document flow so it pushes the page', () => {
    document.body.innerHTML = '<main id="app">content</main>'
    makePlayer([]).start(
      tourOf([step({ id: 's1', type: 'banner', title: 'Heads up' })], {
        kind: 'banner',
        theme: { accent: '#000', position: 'top', banner: { layout: 'inline' } },
      }),
    )
    const banner = document.querySelector('.stept-tour-banner') as HTMLElement
    // First child of <body>, NOT inside the fixed-position overlay root.
    expect(document.body.firstChild).toBe(banner)
    expect(banner.closest('.stept-tour-root')).toBeNull()
  })

  it('derives a readable foreground when the author sets only a background', () => {
    makePlayer([]).start(
      tourOf([step({ id: 's1', type: 'banner', title: 'Pale' })], {
        kind: 'banner',
        theme: { accent: '#000', banner: { background: '#fef3c7' } },
      }),
    )
    const banner = document.querySelector('.stept-tour-banner') as HTMLElement
    expect(banner.style.getPropertyValue('--stept-banner-fg')).toBe('#0f172a')
  })

  it('marks a never-again dismissal so the backend can outrank the frequency rule', () => {
    const events: Recorded[] = []
    makePlayer(events).start(
      tourOf([step({ id: 's1', type: 'banner', title: 'Bye' })], {
        kind: 'banner',
        theme: { accent: '#000', banner: { dismiss: 'never_again' } },
      }),
    )
    const close = document.querySelector('.stept-tour-close') as HTMLElement
    expect(close.getAttribute('aria-label')).toBe('Dismiss and never show again')
    close.click()
    expect(events.find((e) => e.event === 'dismissed')!.meta!.never_again).toBe(true)
  })

  it('leaves an ordinary dismissal unmarked', () => {
    const events: Recorded[] = []
    makePlayer(events).start(
      tourOf([step({ id: 's1', type: 'banner', title: 'Bye' })], { kind: 'banner' }),
    )
    ;(document.querySelector('.stept-tour-close') as HTMLElement).click()
    expect(events.find((e) => e.event === 'dismissed')!.meta!.never_again).toBeUndefined()
  })

  it('uses author button copy and opens a CTA link safely', () => {
    // spyOn, not a spread of `window`: an object literal loses the prototype
    // methods (addEventListener) the player binds on start.
    const open = vi.spyOn(window, 'open').mockReturnValue(null)
    makePlayer([]).start(
      tourOf(
        [
          step({
            id: 's1',
            type: 'banner',
            title: 'Read up',
            cta: { label: 'Read the post', url: 'https://blog.example.com/x' },
            secondary_cta: { label: 'Not now' },
          }),
          step({ id: 's2', type: 'banner', title: 'Second' }),
        ],
        { kind: 'banner' },
      ),
    )
    const banner = document.querySelector('.stept-tour-banner') as HTMLElement
    const buttons = [...banner.querySelectorAll('.stept-tour-btn')] as HTMLElement[]
    expect(buttons.map((b) => b.textContent)).toEqual(['Not now', 'Read the post'])

    buttons[1]!.click()
    expect(open).toHaveBeenCalledWith(
      'https://blog.example.com/x',
      '_blank',
      'noopener,noreferrer',
    )
    // A link CTA reports engagement; it must NOT advance the tour.
    expect(banner.querySelector('strong')!.textContent).toBe('Read up')
    open.mockRestore()
  })

  it('falls back to Next / Got it when no CTA copy is authored', () => {
    makePlayer([]).start(
      tourOf(
        [
          step({ id: 's1', type: 'banner', title: 'One' }),
          step({ id: 's2', type: 'banner', title: 'Two' }),
        ],
        { kind: 'banner' },
      ),
    )
    const banner = () => document.querySelector('.stept-tour-banner') as HTMLElement
    const primary = () => banner().querySelector('.stept-tour-btn.primary') as HTMLElement
    expect(primary().textContent).toBe('Next')
    primary().click()
    expect(primary().textContent).toBe('Got it')
  })

  it('shows a hotspot beacon that opens the tooltip on click', () => {
    document.body.innerHTML = '<button id="save">Save</button>'
    const events: Recorded[] = []
    makePlayer(events).start(
      tourOf([step({ id: 's1', type: 'hotspot', selector: '#save', title: 'Try this' })]),
    )
    const beacon = document.querySelector('.stept-tour-beacon') as HTMLElement
    expect(beacon.hidden).toBe(false)
    expect(tipEl()!.hidden).toBe(true)
    beacon.click()
    expect(tipEl()!.hidden).toBe(false)
    expect(tipText()).toContain('Try this')
  })

  it('badges preview mode and leaves progress untouched', () => {
    const storage = memoryStorage()
    const events: Recorded[] = []
    makePlayer(events, { storage, preview: true }).start(
      tourOf([step({ id: 's1', type: 'modal', title: 'Preview me' })]),
    )
    expect(tipText()).toContain('Preview')
    expect(storage.getItem(tourProgressKey('wk_test'))).toBeNull()
  })

  it('renders step media, resolving backend paths against the API origin', () => {
    const events: Recorded[] = []
    makePlayer(events, { apiBase: 'http://api.test:8600' }).start(
      tourOf([
        step({
          id: 's1',
          type: 'modal',
          title: 'Look',
          media: { type: 'image', url: '/api/widget/media/ws1/public/ws1/2026/07/a.png' },
        }),
        step({
          id: 's2',
          type: 'modal',
          title: 'External',
          media: { type: 'image', url: 'https://cdn.example.com/b.png' },
        }),
      ]),
    )
    const img = tipEl()!.querySelector('img.stept-tour-media') as HTMLImageElement
    expect(img.getAttribute('src')).toBe(
      'http://api.test:8600/api/widget/media/ws1/public/ws1/2026/07/a.png',
    )
    ;(tipEl()!.querySelector('.stept-tour-btn.primary') as HTMLButtonElement).click()
    expect(
      (tipEl()!.querySelector('img.stept-tour-media') as HTMLImageElement).getAttribute('src'),
    ).toBe('https://cdn.example.com/b.png')
  })

  it('honours settings: no progress, no dismiss affordance', () => {
    const events: Recorded[] = []
    makePlayer(events).start(
      tourOf([step({ id: 's1', type: 'modal', title: 'Locked' })], {
        settings: { mode: 'guided', backdrop: false, show_progress: false, dismissable: false },
      }),
    )
    expect(tipEl()!.querySelector('.stept-tour-close')).toBeNull()
    expect(tipEl()!.querySelector('.stept-tour-bar')).toBeNull()
    expect(tipText()).not.toContain('1 of 1')
  })
})

describe('TourPlayer advance semantics', () => {
  it('advances on a click on the target element', () => {
    document.body.innerHTML = '<button id="a">A</button><button id="b">B</button>'
    const events: Recorded[] = []
    makePlayer(events).start(
      tourOf([
        step({ id: 's1', selector: '#a', title: 'First', advance: { on: 'element_click' } }),
        step({ id: 's2', selector: '#b', title: 'Second' }),
      ]),
    )
    expect(tipText()).toContain('First')
    // No Next button: the page click is the advance.
    expect(tipEl()!.querySelector('.stept-tour-btn.primary')).toBeNull()
    document.getElementById('a')!.click()
    expect(tipText()).toContain('Second')
    expect(events.filter((e) => e.event === 'step_viewed').map((e) => e.stepIndex)).toEqual([0, 1])
  })

  it('auto-advances a delay step on its timer', () => {
    vi.useFakeTimers()
    document.body.innerHTML = '<button id="a">A</button>'
    const events: Recorded[] = []
    makePlayer(events).start(
      tourOf([
        step({
          id: 's1',
          selector: '#a',
          title: 'First',
          advance: { on: 'delay', delay_ms: 1500 },
        }),
        step({ id: 's2', type: 'modal', title: 'Second' }),
      ]),
    )
    expect(tipText()).toContain('First')
    vi.advanceTimersByTime(1400)
    expect(tipText()).toContain('First')
    vi.advanceTimersByTime(200)
    expect(tipText()).toContain('Second')
  })

  it('advances an input step once the field has a value', () => {
    document.body.innerHTML = '<input id="email" />'
    const events: Recorded[] = []
    makePlayer(events).start(
      tourOf([
        step({ id: 's1', selector: '#email', title: 'Type', advance: { on: 'input' } }),
        step({ id: 's2', type: 'modal', title: 'Done step' }),
      ]),
    )
    const input = document.getElementById('email') as HTMLInputElement
    input.dispatchEvent(new Event('change', { bubbles: true }))
    expect(tipText()).toContain('Type') // empty value does not advance
    input.value = 'a@b.co'
    input.dispatchEvent(new Event('change', { bubbles: true }))
    expect(tipText()).toContain('Done step')
  })

  it('completes on the last Next and clears the stored progress', () => {
    const storage = memoryStorage()
    const events: Recorded[] = []
    makePlayer(events, { storage }).start(tourOf([step({ id: 's1', type: 'modal', title: 'Only' })]))
    const done = tipEl()!.querySelector('.stept-tour-btn.primary') as HTMLButtonElement
    expect(done.textContent).toBe('Done')
    done.click()
    expect(events.map((e) => e.event)).toEqual(['started', 'step_viewed', 'completed'])
    expect(storage.getItem(tourProgressKey('wk_test'))).toBeNull()
    expect(tipEl()).toBeNull()
  })
})

describe('TourPlayer keyboard + a11y', () => {
  it('navigates with the arrow keys and dismisses with Escape', () => {
    const events: Recorded[] = []
    makePlayer(events).start(
      tourOf([
        step({ id: 's1', type: 'modal', title: 'One' }),
        step({ id: 's2', type: 'modal', title: 'Two' }),
      ]),
    )
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }))
    expect(tipText()).toContain('Two')
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true }))
    expect(tipText()).toContain('One')
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    expect(events.at(-1)!.event).toBe('dismissed')
  })

  it('ignores Escape when the tour is not dismissable', () => {
    const events: Recorded[] = []
    makePlayer(events).start(
      tourOf([step({ id: 's1', type: 'modal', title: 'One' })], {
        settings: { mode: 'guided', backdrop: true, show_progress: true, dismissable: false },
      }),
    )
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    expect(events.some((e) => e.event === 'dismissed')).toBe(false)
    expect(tipEl()).toBeTruthy()
  })

  it('moves focus to the tip and restores it at the end', () => {
    document.body.innerHTML = '<button id="opener">Open</button>'
    const opener = document.getElementById('opener') as HTMLButtonElement
    opener.focus()
    const events: Recorded[] = []
    const p = makePlayer(events)
    p.start(tourOf([step({ id: 's1', type: 'modal', title: 'One' })]))
    expect(document.activeElement).toBe(tipEl())
    p.dismiss()
    expect(document.activeElement).toBe(opener)
  })
})

describe('TourPlayer action steps', () => {
  it('driven mode clicks the element for the user and advances', async () => {
    document.body.innerHTML = '<button id="go">Go</button>'
    const clicks: string[] = []
    document.getElementById('go')!.addEventListener('click', () => clicks.push('go'))
    const events: Recorded[] = []
    makePlayer(events).start(
      tourOf(
        [
          step({ id: 's1', type: 'action', selector: '#go', action: { kind: 'click' } }),
          step({ id: 's2', type: 'modal', title: 'After' }),
        ],
        { settings: { mode: 'driven', backdrop: true, show_progress: true, dismissable: true } },
      ),
    )
    await flush(5)
    expect(clicks).toEqual(['go'])
    expect(tipText()).toContain('After')
  })

  it('driven mode fills a field, firing input + change', async () => {
    document.body.innerHTML = '<input id="email" />'
    const input = document.getElementById('email') as HTMLInputElement
    const fired: string[] = []
    input.addEventListener('input', () => fired.push('input'))
    input.addEventListener('change', () => fired.push('change'))
    const events: Recorded[] = []
    makePlayer(events).start(
      tourOf(
        [
          step({
            id: 's1',
            type: 'action',
            selector: '#email',
            action: { kind: 'fill', value: 'ana@stept.io' },
          }),
        ],
        { settings: { mode: 'driven', backdrop: true, show_progress: true, dismissable: true } },
      ),
    )
    await flush(5)
    expect(input.value).toBe('ana@stept.io')
    expect(fired).toEqual(['input', 'change'])
    expect(events.at(-1)!.event).toBe('completed')
  })

  it('guided mode instructs instead of acting, advancing on the real click', () => {
    document.body.innerHTML = '<button id="go">Go</button>'
    const clicks: string[] = []
    document.getElementById('go')!.addEventListener('click', () => clicks.push('go'))
    const events: Recorded[] = []
    makePlayer(events).start(
      tourOf([
        step({ id: 's1', type: 'action', selector: '#go', action: { kind: 'click' } }),
        step({ id: 's2', type: 'modal', title: 'After' }),
      ]),
    )
    expect(tipText()).toContain('Click the highlighted element')
    expect(clicks).toEqual([]) // nothing performed for the user
    document.getElementById('go')!.click()
    expect(clicks).toEqual(['go'])
    expect(tipText()).toContain('After')
  })

  it('fillElement + performAction work standalone', () => {
    document.body.innerHTML = '<textarea id="t"></textarea><button id="b"></button>'
    const area = document.getElementById('t') as HTMLTextAreaElement
    fillElement(area, 'hello')
    expect(area.value).toBe('hello')
    let clicked = false
    document.getElementById('b')!.addEventListener('click', () => (clicked = true))
    expect(performAction({ kind: 'click' }, document.getElementById('b'), window)).toBe(true)
    expect(clicked).toBe(true)
    expect(performAction({ kind: 'click' }, null, window)).toBe(false)
  })
})

describe('TourPlayer wait + error steps', () => {
  it('emits step_error(not_found) and skips a step whose element is gone', () => {
    document.body.innerHTML = '<button id="b">B</button>'
    const events: Recorded[] = []
    makePlayer(events).start(
      tourOf([
        step({ id: 's1', selector: '#gone', title: 'Missing' }),
        step({ id: 's2', selector: '#b', title: 'Present' }),
      ]),
    )
    const error = events.find((e) => e.event === 'step_error')!
    expect(error.stepIndex).toBe(0)
    expect(error.meta!.reason).toBe('not_found')
    expect(tipText()).toContain('Present')
  })

  it('holds on a wait-for-element step and advances when it appears', async () => {
    const events: Recorded[] = []
    makePlayer(events).start(
      tourOf([
        step({
          id: 's1',
          type: 'wait',
          selector: '#later',
          wait: { for: 'element', selector: '#later', timeout_ms: 2000 },
        }),
        step({ id: 's2', type: 'modal', title: 'Arrived' }),
      ]),
    )
    expect(tipEl()!.hidden).toBe(true) // hidden step, no chrome
    const el = document.createElement('div')
    el.id = 'later'
    document.body.appendChild(el)
    await flush(20)
    expect(tipText()).toContain('Arrived')
    expect(events.some((e) => e.event === 'step_error')).toBe(false)
  })

  it('times out a wait step into step_error and keeps going', async () => {
    const events: Recorded[] = []
    makePlayer(events).start(
      tourOf([
        step({
          id: 's1',
          type: 'wait',
          selector: '#never',
          wait: { for: 'element', timeout_ms: 30 },
        }),
        step({ id: 's2', type: 'modal', title: 'Next up' }),
      ]),
    )
    await flush(80)
    const error = events.find((e) => e.event === 'step_error')!
    expect(error.meta!.reason).toBe('timeout')
    expect(tipText()).toContain('Next up')
  })

  it('advances a wait-for-url step when the SPA navigates', async () => {
    const events: Recorded[] = []
    makePlayer(events).start(
      tourOf([
        step({
          id: 's1',
          type: 'wait',
          wait: { for: 'url', url_pattern: '*/checkout*', timeout_ms: 2000 },
        }),
        step({ id: 's2', type: 'modal', title: 'On checkout' }),
      ]),
    )
    await flush(5)
    expect(tipEl()!.hidden).toBe(true)
    window.history.pushState({}, '', '/checkout/step-2')
    window.dispatchEvent(new Event('stept:locationchange'))
    await flush(5)
    expect(tipText()).toContain('On checkout')
    window.history.pushState({}, '', '/')
  })
})

describe('tour progress persistence', () => {
  it('round-trips through storage and tolerates junk', () => {
    const storage = memoryStorage()
    const key = tourProgressKey('wk_a')
    expect(key).toBe('stept:tour-progress:wk_a')
    writeTourProgress(storage, key, { tourId: 't1', stepIndex: 2, startedAt: 1000 })
    expect(readTourProgress(storage, key)).toEqual({ tourId: 't1', stepIndex: 2, startedAt: 1000 })
    storage.setItem(key, '{nope')
    expect(readTourProgress(storage, key)).toBeNull()
    clearTourProgress(storage, key)
    expect(readTourProgress(storage, key)).toBeNull()
    expect(readTourProgress(null, key)).toBeNull()
  })

  it('resumes mid-tour after a reload WITHOUT re-emitting started', () => {
    const storage = memoryStorage()
    const tour = tourOf([
      step({ id: 's1', type: 'modal', title: 'One' }),
      step({ id: 's2', type: 'modal', title: 'Two' }),
      step({ id: 's3', type: 'modal', title: 'Three' }),
    ])
    const first: Recorded[] = []
    const p1 = makePlayer(first, { storage })
    p1.start(tour)
    p1.next()
    expect(tipText()).toContain('Two')
    p1.stop() // the page reloads mid-tour

    const second: Recorded[] = []
    const p2 = makePlayer(second, { storage })
    p2.start(tour)
    expect(tipText()).toContain('Two')
    expect(p2.stepIndex).toBe(1)
    expect(second.map((e) => e.event)).toEqual(['step_viewed'])
    expect(second.some((e) => e.event === 'started')).toBe(false)
  })

  it('emits started again for a different tour', () => {
    const storage = memoryStorage()
    writeTourProgress(storage, tourProgressKey('wk_test'), {
      tourId: 'other-tour',
      stepIndex: 3,
      startedAt: 1,
    })
    const events: Recorded[] = []
    makePlayer(events, { storage }).start(tourOf([step({ id: 's1', type: 'modal', title: 'One' })]))
    expect(events[0]!.event).toBe('started')
    expect(tipText()).toContain('One')
  })
})
