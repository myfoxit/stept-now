import { describe, expect, it } from 'vitest'

import {
  addFallback,
  byDaySeries,
  duplicatePayload,
  emptyStep,
  isEmptySeries,
  isoToLocalInput,
  localInputToIso,
  moveStep,
  prependEvent,
  serializeFilters,
  serializeStep,
  serializeTour,
  toStepDraft,
  toTourDraft,
  validateSteps,
  type FilterDraft,
} from './lib'
import { makeEvent, makeEventsPage, makeStep, makeTour } from './test-utils'

describe('moveStep', () => {
  const list = ['a', 'b', 'c']

  it('moves an item down', () => {
    expect(moveStep(list, 0, 1)).toEqual(['b', 'a', 'c'])
  })

  it('moves an item up', () => {
    expect(moveStep(list, 2, 1)).toEqual(['a', 'c', 'b'])
  })

  it('is a no-op for out-of-bounds or same index', () => {
    expect(moveStep(list, 0, 0)).toEqual(['a', 'b', 'c'])
    expect(moveStep(list, 0, 3)).toEqual(['a', 'b', 'c'])
    expect(moveStep(list, -1, 1)).toEqual(['a', 'b', 'c'])
  })

  it('does not mutate the input array', () => {
    const original = [...list]
    moveStep(list, 0, 2)
    expect(list).toEqual(original)
  })
})

describe('serializeStep', () => {
  it('trims the selector, omits a null id and defaults the advance mode', () => {
    const out = serializeStep({ ...emptyStep(), selector: '  #btn  ', title: 'Hi' })
    expect(out.selector).toBe('#btn')
    expect('id' in out).toBe(false)
    expect(out.advance).toEqual({ on: 'button' })
    expect(out.action).toBeUndefined()
    expect(out.wait).toBeUndefined()
  })

  it('emits delay_ms only for delay advance', () => {
    const base = { ...emptyStep(), selector: '#a', delayMs: '2500' }
    expect(serializeStep({ ...base, advanceOn: 'delay' }).advance).toEqual({
      on: 'delay',
      delay_ms: 2500,
    })
    expect(serializeStep({ ...base, advanceOn: 'element_click' }).advance).toEqual({
      on: 'element_click',
    })
  })

  it('emits an action block for action steps and only the fields that kind allows', () => {
    const fill = serializeStep({
      ...emptyStep('action'),
      selector: '#email',
      actionKind: 'fill',
      actionValue: 'ada@acme.test',
      actionUrl: 'ignored',
    })
    expect(fill.action).toEqual({ kind: 'fill', value: 'ada@acme.test' })
    expect(fill.wait).toBeUndefined()

    const navigate = serializeStep({
      ...emptyStep('action'),
      selector: '#go',
      actionKind: 'navigate',
      actionUrl: ' /billing ',
      actionValue: 'ignored',
    })
    expect(navigate.action).toEqual({ kind: 'navigate', url: '/billing' })
  })

  it('emits a wait block for wait steps, falling back to the step selector', () => {
    const element = serializeStep({
      ...emptyStep('wait'),
      selector: '#ready',
      waitTimeoutMs: '4000',
    })
    expect(element.wait).toEqual({ for: 'element', timeout_ms: 4000, selector: '#ready' })

    const url = serializeStep({
      ...emptyStep('wait'),
      waitFor: 'url',
      waitUrlPattern: '*/done*',
    })
    expect(url.wait).toEqual({ for: 'url', timeout_ms: 10000, url_pattern: '*/done*' })
    expect(url.action).toBeUndefined()
  })

  it('emits media only when a url is present', () => {
    expect(serializeStep({ ...emptyStep(), selector: '#a' }).media).toBeUndefined()
    expect(
      serializeStep({ ...emptyStep(), selector: '#a', mediaType: 'video', mediaUrl: ' /m.mp4 ' })
        .media
    ).toEqual({ type: 'video', url: '/m.mp4' })
  })

  it('carries the recorder target and screenshot key through an edit untouched', () => {
    const target = { selectors: [{ kind: 'css', value: '#signup', score: 0.9 }], text: 'Sign up' }
    const stored = makeStep({
      id: 's9',
      target,
      screenshot_key: 'public/w1/2026/07/abc.png',
      fallback_selectors: ['[data-testid=signup]'],
      text_hint: 'Sign up',
    })

    // Load into the editor, change only the body, save again.
    const draft = toStepDraft(stored)
    const out = serializeStep({ ...draft, body: 'edited' })

    expect(out.target).toEqual(target)
    expect(out.screenshot_key).toBe('public/w1/2026/07/abc.png')
    expect(out.fallback_selectors).toEqual(['[data-testid=signup]'])
    expect(out.text_hint).toBe('Sign up')
    expect(out.id).toBe('s9')
    expect(out.body).toBe('edited')
  })

  it('caps fallback selectors at five and clips the text hint', () => {
    const out = serializeStep({
      ...emptyStep(),
      selector: '#a',
      fallbackSelectors: ['a', 'b', 'c', 'd', 'e', 'f'],
      textHint: 'x'.repeat(120),
    })
    expect(out.fallback_selectors).toHaveLength(5)
    expect(out.text_hint).toHaveLength(80)
  })
})

describe('validateSteps', () => {
  it('requires a selector for anchored types only', () => {
    expect(validateSteps([{ ...emptyStep('tooltip') }])).toMatch(/Step 1 needs a CSS selector/)
    expect(validateSteps([{ ...emptyStep('modal'), title: 'Hi' }])).toBeNull()
  })

  it('checks the conditional fields of action and wait steps', () => {
    expect(validateSteps([{ ...emptyStep('action'), selector: '#a', actionKind: 'fill' }])).toMatch(
      /needs a value/
    )
    expect(validateSteps([{ ...emptyStep('wait'), waitFor: 'url', waitUrlPattern: '' }])).toMatch(
      /needs a pattern/
    )
    expect(validateSteps([{ ...emptyStep('wait'), waitSelector: '#ok' }])).toBeNull()
  })

  it('rejects a delay below the backend minimum', () => {
    expect(
      validateSteps([{ ...emptyStep(), selector: '#a', advanceOn: 'delay', delayMs: '50' }])
    ).toMatch(/at least 100ms/)
  })
})

describe('addFallback', () => {
  it('adds, trims, de-duplicates and caps at five', () => {
    expect(addFallback([], ' #a ')).toEqual(['#a'])
    expect(addFallback(['#a'], '#a')).toEqual(['#a'])
    expect(addFallback(['#a'], '  ')).toEqual(['#a'])
    const five = ['a', 'b', 'c', 'd', 'e']
    expect(addFallback(five, 'f')).toBe(five)
  })
})

describe('serializeFilters', () => {
  const row = (over: Partial<FilterDraft>): FilterDraft => ({
    key: 'k',
    field: 'email',
    attrKey: '',
    op: 'eq',
    value: '',
    ...over,
  })

  it('expands attribute rows and coerces booleans', () => {
    expect(
      serializeFilters([
        row({ field: 'attributes', attrKey: ' plan ', op: 'contains', value: 'pro' }),
        row({ field: 'verified', op: 'eq', value: 'true' }),
      ])
    ).toEqual([
      { field: 'attributes.plan', op: 'contains', value: 'pro' },
      { field: 'verified', op: 'eq', value: true },
    ])
  })

  it('drops incomplete rows and sends no value for unary ops', () => {
    expect(
      serializeFilters([
        row({ field: 'email', op: 'eq', value: '   ' }),
        row({ field: 'attributes', attrKey: '', op: 'exists' }),
        row({ field: 'external_id', op: 'not_exists' }),
      ])
    ).toEqual([{ field: 'external_id', op: 'not_exists' }])
  })
})

describe('serializeTour', () => {
  it('always sends audience, schedule, frequency and settings', () => {
    const draft = {
      ...toTourDraft(makeTour()),
      audienceType: 'filters' as const,
      filters: [
        { key: 'k', field: 'attributes', attrKey: 'plan', op: 'eq' as const, value: 'pro' },
      ],
      frequencyType: 'every_time' as const,
      cooldownHours: '12',
      priority: '5',
      mode: 'driven' as const,
      backdrop: false,
    }
    const body = serializeTour(draft, [])

    expect(body.audience).toEqual({
      type: 'filters',
      filters: [{ field: 'attributes.plan', op: 'eq', value: 'pro' }],
    })
    expect(body.frequency).toEqual({ type: 'every_time', cooldown_hours: 12 })
    expect(body.priority).toBe(5)
    expect(body.settings).toEqual({
      mode: 'driven',
      backdrop: false,
      show_progress: true,
      dismissable: true,
    })
    expect(body.theme).toEqual({ accent: '#6366f1' })
  })

  it('sends an empty filter list when the audience is everyone, and no cooldown otherwise', () => {
    const draft = {
      ...toTourDraft(makeTour()),
      audienceType: 'all' as const,
      filters: [{ key: 'k', field: 'email', attrKey: '', op: 'eq' as const, value: 'a@b.co' }],
      cooldownHours: '12',
    }
    const body = serializeTour(draft, [])
    expect(body.audience).toEqual({ type: 'all', filters: [] })
    expect(body.frequency).toEqual({ type: 'until_dismissed', cooldown_hours: null })
  })

  it('adds the banner position to the theme only for banner tours', () => {
    const draft = toTourDraft(
      makeTour({ kind: 'banner', theme: { accent: '#111', position: 'top' } })
    )
    expect(serializeTour(draft, []).theme).toEqual({ accent: '#111', position: 'top' })
    expect(serializeTour({ ...draft, kind: 'flow' }, []).theme).toEqual({ accent: '#111' })
  })

  it('round-trips a schedule through the datetime-local inputs', () => {
    const iso = '2026-08-01T09:30:00.000Z'
    const local = isoToLocalInput(iso)
    expect(local).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/)
    expect(localInputToIso(local)).toBe(iso)
    expect(localInputToIso('')).toBeNull()
    expect(isoToLocalInput(null)).toBe('')

    const draft = { ...toTourDraft(makeTour()), startAt: local, endAt: '' }
    expect(serializeTour(draft, []).schedule).toEqual({ start_at: iso, end_at: null })
  })
})

describe('duplicatePayload', () => {
  it('names the copy and strips step ids so the two tours never share them', () => {
    const tour = makeTour({ steps: [makeStep({ id: 's1' }), makeStep({ id: 's2' })] })
    const payload = duplicatePayload(tour)

    expect(payload.name).toBe('Welcome tour (copy)')
    expect(payload.kind).toBe('flow')
    expect(payload.steps).toHaveLength(2)
    expect(payload.steps!.every((step) => !('id' in step))).toBe(true)
    expect(payload.trigger).toEqual(tour.trigger)
    expect(payload.audience).toEqual(tour.audience)
  })
})

describe('analytics helpers', () => {
  it('trims the by-day window to the active range with one day of padding', () => {
    const days = [
      { date: '2026-07-01', starts: 0, completions: 0 },
      { date: '2026-07-02', starts: 0, completions: 0 },
      { date: '2026-07-03', starts: 4, completions: 1 },
      { date: '2026-07-04', starts: 0, completions: 0 },
      { date: '2026-07-05', starts: 0, completions: 0 },
    ]
    expect(byDaySeries(days).map((d) => d.date)).toEqual(['2026-07-02', '2026-07-03', '2026-07-04'])
    expect(isEmptySeries(days)).toBe(false)
    expect(isEmptySeries([{ date: '2026-07-01', starts: 0, completions: 0 }])).toBe(true)
    expect(byDaySeries(undefined)).toEqual([])
  })

  it('prepends live events without growing the page or duplicating ids', () => {
    const page = makeEventsPage({ items: [makeEvent({ id: 'e1' })], limit: 2, total: 1 })
    const grown = prependEvent(page, makeEvent({ id: 'e2', event: 'completed' }))
    expect(grown.items.map((e) => e.id)).toEqual(['e2', 'e1'])
    expect(grown.total).toBe(2)

    const trimmed = prependEvent(grown, makeEvent({ id: 'e3' }))
    expect(trimmed.items.map((e) => e.id)).toEqual(['e3', 'e2'])

    expect(prependEvent(grown, makeEvent({ id: 'e2' }))).toBe(grown)
  })
})
