import { buildTarget } from '@stept/dom-capture'
import { afterEach, describe, expect, it } from 'vitest'

import {
  parseSelectorString,
  resolveStepTarget,
  stepNeedsTarget,
  stepTarget,
  waitForTarget,
} from './dom-target'
import type { TourStep } from './types'

function step(partial: Partial<TourStep> = {}): TourStep {
  return {
    id: 's1',
    selector: '',
    title: 'Step',
    body: '',
    placement: 'auto',
    ...partial,
  }
}

afterEach(() => {
  document.body.innerHTML = ''
})

describe('parseSelectorString', () => {
  it('recognises the DevTools kind prefixes and defaults to css', () => {
    expect(parseSelectorString('aria/Save[role="button"]').kind).toBe('aria')
    expect(parseSelectorString('text/Save').kind).toBe('text')
    expect(parseSelectorString('xpath//html/body').kind).toBe('xpath')
    expect(parseSelectorString('#save')).toEqual({ kind: 'css', value: '#save' })
  })
})

describe('stepTarget', () => {
  it('synthesizes a ranked stack from the simple projection, primary first', () => {
    const target = stepTarget(
      step({ selector: '#save', fallback_selectors: ['button.save', 'aria/Save'] }),
    )
    expect(target.selectors.map((s) => s.value)).toEqual(['#save', 'button.save', 'aria/Save'])
    expect(target.selectors[0]!.score).toBeGreaterThan(target.selectors[1]!.score)
    expect(target.selectors[2]!.kind).toBe('aria')
  })

  it('passes a recorded rich target through untouched', () => {
    document.body.innerHTML = '<button id="save">Save</button>'
    const recorded = buildTarget(document.getElementById('save')!)
    expect(stepTarget(step({ selector: '#other', target: recorded }))).toBe(recorded)
  })
})

describe('stepNeedsTarget', () => {
  it('is true for anchored types with a selector and false for floating ones', () => {
    expect(stepNeedsTarget(step({ type: 'tooltip', selector: '#a' }))).toBe(true)
    expect(stepNeedsTarget(step({ type: 'hotspot', selector: '#a' }))).toBe(true)
    expect(stepNeedsTarget(step({ type: 'modal', selector: '#a' }))).toBe(false)
    expect(stepNeedsTarget(step({ type: 'banner' }))).toBe(false)
    expect(stepNeedsTarget(step({ type: 'tooltip', selector: '' }))).toBe(false)
  })

  it('is true only for wait-for-element steps', () => {
    const element = step({
      type: 'wait',
      selector: '#a',
      wait: { for: 'element', timeout_ms: 100 },
    })
    const url = step({
      type: 'wait',
      wait: { for: 'url', url_pattern: '*/done', timeout_ms: 100 },
    })
    expect(stepNeedsTarget(element)).toBe(true)
    expect(stepNeedsTarget(url)).toBe(false)
  })
})

describe('resolveStepTarget', () => {
  it('resolves a recorded rich target via the primary selector (not healed)', () => {
    document.body.innerHTML = '<main><button id="save" data-testid="save">Save</button></main>'
    const recorded = buildTarget(document.getElementById('save')!)
    const result = resolveStepTarget(step({ selector: '#save', target: recorded }))
    expect(result.el).toBe(document.getElementById('save'))
    expect(result.healed).toBe(false)
    expect(result.via).toBe('primary')
  })

  it('heals a recorded target whose selectors all rotted (fingerprint hit)', () => {
    document.body.innerHTML = '<main><button id="save-v1" name="save">Save</button></main>'
    const recorded = buildTarget(document.getElementById('save-v1')!)
    // The app ships a new build: every recorded selector now misses.
    document.body.innerHTML = '<main><button id="save-v2" name="save">Save</button></main>'
    const result = resolveStepTarget(step({ selector: '#save-v1', target: recorded }))
    expect(result.el).toBe(document.getElementById('save-v2'))
    expect(result.healed).toBe(true)
    expect(result.via).not.toBe('primary')
  })

  it('resolves a projection-only step via its primary selector', () => {
    document.body.innerHTML = '<button id="save">Save</button>'
    const result = resolveStepTarget(step({ selector: '#save', text_hint: 'Save' }))
    expect(result.el).toBe(document.getElementById('save'))
    expect(result.healed).toBe(false)
    expect(result.via).toBe('primary')
  })

  it('falls back to the next selector and flags the hit as healed', () => {
    document.body.innerHTML = '<button class="save">Save</button>'
    const result = resolveStepTarget(
      step({ selector: '#gone', fallback_selectors: ['.save'] }),
    )
    expect(result.el).toBe(document.querySelector('.save'))
    expect(result.healed).toBe(true)
    expect(result.via).toBe('fallback')
  })

  it('falls back to the recorded text hint when every selector misses', () => {
    document.body.innerHTML = '<button class="cta">Create workspace</button>'
    const result = resolveStepTarget(
      step({ selector: '#gone', fallback_selectors: ['.gone'], text_hint: 'Create workspace' }),
    )
    expect(result.el).toBe(document.querySelector('.cta'))
    expect(result.healed).toBe(true)
    expect(result.via).toBe('text_hint')
  })

  it('reports not_found when nothing matches', () => {
    document.body.innerHTML = '<div>nothing here</div>'
    const result = resolveStepTarget(step({ selector: '#gone', text_hint: 'Nope' }))
    expect(result.el).toBeNull()
    expect(result.healed).toBe(false)
    expect(result.reason).toBe('not_found')
  })

  it('reports in_iframe for a cross-frame target instead of scanning the top document', () => {
    document.body.innerHTML = '<button id="save">Save</button>'
    const target = { ...buildTarget(document.getElementById('save')!), frame: [{ name: 'checkout' }] }
    const result = resolveStepTarget(step({ selector: '#save', target }))
    expect(result.el).toBeNull()
    expect(result.reason).toBe('in_iframe')
  })
})

describe('waitForTarget', () => {
  it('resolves synchronously when the element is already there', async () => {
    document.body.innerHTML = '<button id="save">Save</button>'
    const result = await waitForTarget(step({ selector: '#save' }), 1000)
    expect(result.el).toBe(document.getElementById('save'))
  })

  it('resolves early once the element appears (mutation observer)', async () => {
    const pending = waitForTarget(step({ selector: '#late' }), 2000, { pollMs: 20 })
    const button = document.createElement('button')
    button.id = 'late'
    document.body.appendChild(button)
    const result = await pending
    expect(result.el).toBe(button)
    expect(result.healed).toBe(false)
  })

  it('resolves with a timeout reason when the element never appears', async () => {
    const result = await waitForTarget(step({ selector: '#never' }), 40, { pollMs: 10 })
    expect(result.el).toBeNull()
    expect(result.reason).toBe('timeout')
  })

  it('fails fast for an in_iframe step rather than burning the budget', async () => {
    document.body.innerHTML = '<button id="save">Save</button>'
    const target = { ...buildTarget(document.getElementById('save')!), frame: [{ name: 'f' }] }
    const started = Date.now()
    const result = await waitForTarget(step({ selector: '#save', target }), 5000)
    expect(result.reason).toBe('in_iframe')
    expect(Date.now() - started).toBeLessThan(1000)
  })
})
