/**
 * Host-side loader behaviour: the tour pill (offer / resume / progress),
 * messenger collapse + restore around a tour, the `tour:state` /
 * `tour:resume` bridge messages and the autostart policy.
 *
 * The loader is exercised as a real WidgetHost against jsdom: READY arrives as
 * a genuine MessageEvent from the iframe's contentWindow, fetch is routed by
 * URL, and localStorage is the same store the TourPlayer persists into — so
 * these tests cover the actual loader↔player resume handshake.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { WidgetHost } from './loader'
import { envelope, MSG } from './protocol'
import { tourProgressKey, readTourProgress, writeTourProgress } from './tour-player'
import type { ExperiencesResponse, Tour } from './types'

// --- storage -----------------------------------------------------------------

/** The node/jsdom localStorage here is a stub without methods — use our own. */
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

let store: Storage

// --- fetch routing -----------------------------------------------------------

let experiencesData: ExperiencesResponse = { tours: [], checklists: [], surveys: [] }
let tourById: Record<string, Tour> = {}
let fetchCalls: Array<{ url: string; body: Record<string, unknown> | null }> = []

function jsonRes(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function installFetch(): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      fetchCalls.push({
        url,
        body: init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : null,
      })
      if (url.includes('/api/widget/experiences')) return jsonRes(experiencesData)
      if (url.includes('/api/widget/campaigns')) return jsonRes([])
      if (url.includes('/events?')) return jsonRes({})
      const m = /\/api\/widget\/tours\/([^/?]+)/.exec(url)
      if (m) {
        const tour = tourById[decodeURIComponent(m[1]!)]
        return tour ? jsonRes(tour) : jsonRes({ detail: 'not found' }, 404)
      }
      return jsonRes({})
    }),
  )
}

/** Fetch bodies posted to the tour telemetry endpoint. */
function telemetry(): Array<Record<string, unknown>> {
  return fetchCalls.filter((c) => c.url.includes('/events?') && c.body).map((c) => c.body!)
}

// --- host scaffolding --------------------------------------------------------

interface Posted {
  type: string
  payload: Record<string, unknown>
}

let host: WidgetHost | null = null

const flush = (ms = 5): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms))

async function bootHost(
  settings: Record<string, unknown> = {},
  readyPayload: Record<string, unknown> = {},
): Promise<{ host: WidgetHost; frame: HTMLIFrameElement; posts: Posted[] }> {
  host = new WidgetHost({ workspaceKey: 'wk_test', apiBase: 'http://api.test', ...settings })
  host.init()
  const frame = document.getElementById('stept-frame') as HTMLIFrameElement
  const posts: Posted[] = []
  vi.spyOn(frame.contentWindow!, 'postMessage').mockImplementation(((data: unknown) => {
    const env = data as { source?: string; type?: string; payload?: unknown }
    if (env && env.source === 'stept-widget') {
      posts.push({ type: env.type!, payload: (env.payload ?? {}) as Record<string, unknown> })
    }
  }) as never)
  window.dispatchEvent(
    new MessageEvent('message', {
      data: envelope(MSG.READY, { token: 'tok', ...readyPayload }),
      source: frame.contentWindow,
    }),
  )
  await flush(10)
  return { host, frame, posts }
}

function fromApp(frame: HTMLIFrameElement, type: Parameters<typeof envelope>[0], payload: unknown): void {
  window.dispatchEvent(
    new MessageEvent('message', { data: envelope(type, payload), source: frame.contentWindow }),
  )
}

function makeTour(id: string, titles: string[]): Tour {
  return {
    id,
    name: `Tour ${id}`,
    version: 1,
    theme: { accent: '#5b46e5' },
    steps: titles.map((title, i) => ({
      id: `${id}-s${i}`,
      type: 'modal' as const,
      selector: '',
      title,
      body: '',
      placement: 'auto' as const,
    })),
  }
}

const pill = (): HTMLElement | null => document.getElementById('stept-tour-pill')
const pillButton = (label: string): HTMLButtonElement => {
  const hit = [...(pill()?.querySelectorAll('button') ?? [])].find((b) => b.textContent === label)
  if (!hit) throw new Error(`no "${label}" pill button`)
  return hit as HTMLButtonElement
}
const tipText = (): string => document.querySelector('.stept-tour-tip')?.textContent ?? ''
const tipButton = (label: string): HTMLButtonElement => {
  const hit = [...document.querySelectorAll('.stept-tour-tip button')].find(
    (b) => b.textContent === label,
  )
  if (!hit) throw new Error(`no "${label}" tour button`)
  return hit as HTMLButtonElement
}
const states = (posts: Posted[]): Array<Record<string, unknown>> =>
  posts.filter((p) => p.type === MSG.TOUR_STATE).map((p) => p.payload)

beforeEach(() => {
  experiencesData = { tours: [], checklists: [], surveys: [] }
  tourById = {}
  fetchCalls = []
  store = memoryStorage()
  Object.defineProperty(window, 'localStorage', { value: store, configurable: true })
  installFetch()
})

afterEach(() => {
  host?.shutdown()
  host = null
  document.body.innerHTML = ''
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('autostart policy', () => {
  it('offers a pushed tour as a pill by default (ask) instead of hijacking the page', async () => {
    const tour = makeTour('tour-1', ['One', 'Two'])
    experiencesData = { tours: [tour], checklists: [], surveys: [] }
    tourById['tour-1'] = tour
    const { posts } = await bootHost()

    expect(document.querySelector('.stept-tour-tip')).toBeNull() // not playing
    expect(pill()!.textContent).toContain('Tour tour-1')
    pillButton('Start').click()
    expect(tipText()).toContain('One')
    expect(states(posts).some((s) => s.status === 'started' && s.tourId === 'tour-1')).toBe(true)
  })

  it('marks a declined offer seen so navigation does not re-offer it', async () => {
    const tour = makeTour('tour-1', ['One'])
    experiencesData = { tours: [tour], checklists: [], surveys: [] }
    await bootHost()
    pill()!.querySelector<HTMLButtonElement>('.stept-pill-close')!.click()
    expect(pill()).toBeNull()
    expect(store.getItem('stept:tours-seen:wk_test')).toContain('tour-1')
  })

  it('plays a pushed tour immediately under policy auto', async () => {
    const tour = makeTour('tour-1', ['One'])
    experiencesData = { tours: [tour], checklists: [], surveys: [] }
    tourById['tour-1'] = tour
    await bootHost({ tourAutostartPolicy: 'auto' })
    expect(tipText()).toContain('One')
  })

  it('suppresses pushed tours entirely under policy never', async () => {
    const tour = makeTour('tour-1', ['One'])
    experiencesData = { tours: [tour], checklists: [], surveys: [] }
    tourById['tour-1'] = tour
    await bootHost({ tourAutostartPolicy: 'never' })
    expect(document.querySelector('.stept-tour-tip')).toBeNull()
    expect(pill()).toBeNull()
  })

  it('reads the policy from the READY payload when the host page sets none', async () => {
    const tour = makeTour('tour-1', ['One'])
    experiencesData = { tours: [tour], checklists: [], surveys: [] }
    tourById['tour-1'] = tour
    await bootHost({}, { tour_autostart_policy: 'never' })
    expect(document.querySelector('.stept-tour-tip')).toBeNull()
    expect(pill()).toBeNull()
  })
})

describe('messenger collapse + restore around a tour', () => {
  it('collapses the open panel to a progress pill and restores it after completion', async () => {
    const tour = makeTour('tour-1', ['One', 'Two'])
    tourById['tour-1'] = tour
    const { frame, posts } = await bootHost()

    host!.openPanel()
    expect(frame.classList.contains('stept-open')).toBe(true)

    await host!.startTour('tour-1')
    expect(frame.classList.contains('stept-open')).toBe(false) // panel out of the way
    expect(pill()!.textContent).toContain('1/2')
    expect(pill()!.textContent).toContain('Stop')

    tipButton('Next').click()
    expect(pill()!.textContent).toContain('2/2') // pill tracks the step
    tipButton('Done').click()

    expect(pill()).toBeNull()
    expect(frame.classList.contains('stept-open')).toBe(true) // restored
    expect(states(posts).map((s) => s.status)).toContain('completed')
  })

  it('Stop on the pill dismisses the tour and reports dismissed (not step_error)', async () => {
    const tour = makeTour('tour-1', ['One', 'Two'])
    tourById['tour-1'] = tour
    const { posts } = await bootHost()
    await host!.startTour('tour-1')

    pillButton('Stop').click()
    expect(document.querySelector('.stept-tour-tip')).toBeNull()
    expect(states(posts).map((s) => s.status)).toContain('dismissed')
    const events = telemetry().map((b) => b.event)
    expect(events).toContain('dismissed')
    expect(events).not.toContain('step_error')
  })
})

describe('resume across loads and tabs', () => {
  const saveProgress = (extra: Record<string, unknown> = {}): void => {
    writeTourProgress(store, tourProgressKey('wk_test'), {
      tourId: 'tour-1',
      stepIndex: 1,
      startedAt: Date.now() - 60_000,
      updatedAt: Date.now() - 60_000,
      ...extra,
    } as never)
  }

  it('offers a resume pill for progress the visitor left behind (new tab, reload)', async () => {
    tourById['tour-1'] = makeTour('tour-1', ['One', 'Two'])
    saveProgress()
    await bootHost()

    expect(document.querySelector('.stept-tour-tip')).toBeNull() // no step-1 restart
    expect(pill()!.textContent).toContain('Continue tour — step 2 of 2')
    pillButton('Continue').click()
    expect(tipText()).toContain('Two') // resumed at the saved step
    // Resuming never re-emits started — that would inflate start counts.
    expect(telemetry().map((b) => b.event)).not.toContain('started')
  })

  it('continues automatically when the player itself navigated (flagged progress)', async () => {
    tourById['tour-1'] = makeTour('tour-1', ['One', 'Two'])
    saveProgress({ navigating: true })
    await bootHost()

    expect(tipText()).toContain('Two')
    expect(pill()!.textContent).toContain('2/2') // straight to the progress pill
  })

  it('declining the resume pill reports dismissed and clears the record', async () => {
    tourById['tour-1'] = makeTour('tour-1', ['One', 'Two'])
    saveProgress()
    await bootHost()

    pill()!.querySelector<HTMLButtonElement>('.stept-pill-close')!.click()
    await flush()
    expect(pill()).toBeNull()
    const dismissed = telemetry().find((b) => b.event === 'dismissed')!
    expect(dismissed.step_index).toBe(1)
    expect((dismissed.meta as Record<string, unknown>).resume_declined).toBe(true)
    expect(readTourProgress(store, tourProgressKey('wk_test'))).toBeNull()
    expect(store.getItem('stept:tours-seen:wk_test')).toContain('tour-1')
  })

  it('never resurrects a tour whose id is already completed/dismissed (zombie dedupe)', async () => {
    tourById['tour-1'] = makeTour('tour-1', ['One', 'Two'])
    saveProgress()
    store.setItem('stept:tours-seen:wk_test', JSON.stringify(['tour-1']))
    await bootHost()

    expect(pill()).toBeNull()
    expect(document.querySelector('.stept-tour-tip')).toBeNull()
    expect(readTourProgress(store, tourProgressKey('wk_test'))).toBeNull()
    expect(fetchCalls.some((c) => /\/api\/widget\/tours\/tour-1(\?|$)/.test(c.url))).toBe(false)
  })

  it('handles stept:tour:resume from the messenger app', async () => {
    tourById['tour-1'] = makeTour('tour-1', ['One', 'Two'])
    saveProgress()
    const { frame } = await bootHost()
    expect(pill()!.textContent).toContain('Continue tour')

    fromApp(frame, MSG.TOUR_RESUME, { tourId: 'tour-1' })
    await flush(10)
    expect(tipText()).toContain('Two')
  })

  it('replaces an active tour cleanly on an explicit start and dedupes a re-push', async () => {
    const one = makeTour('tour-1', ['One A', 'One B'])
    const two = makeTour('tour-2', ['Two A'])
    tourById['tour-1'] = one
    tourById['tour-2'] = two
    const { posts } = await bootHost()

    await host!.startTour('tour-1')
    tipButton('Next').click()
    // A re-push of the SAME tour must not restart it at step 1.
    await host!.startTour('tour-1')
    expect(tipText()).toContain('One B')

    // A different tour replaces it: the old one is dismissed under ITS id.
    await host!.startTour('tour-2')
    expect(tipText()).toContain('Two A')
    const dismissed = states(posts).find((s) => s.status === 'dismissed')
    expect(dismissed?.tourId).toBe('tour-1')
  })
})
