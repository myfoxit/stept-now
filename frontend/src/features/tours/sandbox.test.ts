import { describe, expect, it } from 'vitest'

import { emptyStep, targetBBox, type StepDraft } from './lib'
import {
  buildSandboxDoc,
  overlayBox,
  SANDBOX_ATTR,
  sandboxCardSide,
  screenFor,
  snapshotWarnings,
  stepBox,
  type PageSnapshot,
} from './sandbox'

function snapshot(over: Partial<PageSnapshot> = {}): PageSnapshot {
  return {
    version: 1,
    html: '<html><body><h1>Billing</h1></body></html>',
    css: ['.a{color:red}'],
    url: 'https://app.example.com/billing',
    title: 'Billing',
    ...over,
  }
}

function draftWith(over: Partial<StepDraft> = {}): StepDraft {
  return { ...emptyStep(), ...over }
}

describe('buildSandboxDoc', () => {
  it('assembles one self-contained document', () => {
    const doc = buildSandboxDoc(snapshot())
    expect(doc.startsWith('<!doctype html>')).toBe(true)
    expect(doc).toContain('<h1>Billing</h1>')
    expect(doc).toContain('.a{color:red}')
    expect(doc).toContain('<base href="https://app.example.com/billing">')
  })

  it('carries a script-blocking CSP', () => {
    const doc = buildSandboxDoc(snapshot())
    expect(doc).toContain("script-src 'none'")
    expect(doc).toContain("form-action 'none'")
  })

  it('escapes a hostile title or url instead of letting it break out', () => {
    const doc = buildSandboxDoc(
      snapshot({ title: '</title><script>x()</script>', url: '"><script>x()</script>' })
    )
    expect(doc).not.toContain('<script>x()</script>')
    expect(doc).toContain('&lt;script&gt;')
  })

  it('survives an envelope with no css, url or title', () => {
    const doc = buildSandboxDoc({ html: '<html><body>bare</body></html>' })
    expect(doc).toContain('bare')
    expect(doc).not.toContain('<base')
  })

  it('locks the host iframe down with an empty sandbox attribute', () => {
    // Present but empty: no scripts, no same-origin, no form submission. Any
    // token added here would let captured markup run on a Stept origin.
    expect(SANDBOX_ATTR).toBe('')
  })
})

describe('snapshotWarnings', () => {
  it('is silent for a clean capture', () => {
    expect(snapshotWarnings(snapshot())).toEqual([])
  })

  it('flags unreadable stylesheets, frames and canvases', () => {
    const warnings = snapshotWarnings(
      snapshot({
        blockedStyles: ['https://cdn.example.com/a.css'],
        omitted: { frames: 2, canvases: 1, masked: 3 },
      })
    )
    expect(warnings).toHaveLength(3)
    expect(warnings[0]).toMatch(/1 stylesheet could not be read/)
    expect(warnings[1]).toMatch(/2 embedded frame/)
    expect(warnings[2]).toMatch(/1 canvas/)
  })

  it('does not treat masking as a defect — it is the point', () => {
    const warnings = snapshotWarnings(
      snapshot({ omitted: { frames: 0, canvases: 0, masked: 5 } })
    )
    expect(warnings).toEqual([])
  })
})

describe('overlayBox', () => {
  it('converts recorded pixels into percentages of the recorded viewport', () => {
    const box = targetBBox({ bbox: { x: 320, y: 200, w: 128, h: 40, viewport: { w: 1280, h: 800 } } })!
    expect(overlayBox(box)).toEqual({ left: 25, top: 25, width: 10, height: 5 })
  })
})

describe('sandboxCardSide', () => {
  it('sits below an element near the top', () => {
    expect(sandboxCardSide({ left: 10, top: 10, width: 20, height: 5 })).toBe('bottom')
  })

  it('flips above an element pinned to the bottom edge', () => {
    expect(sandboxCardSide({ left: 10, top: 88, width: 20, height: 10 })).toBe('top')
  })

  it('keeps the roomier side when neither fits the card', () => {
    // 4% below, 88% above → above wins even though neither clears the card.
    expect(sandboxCardSide({ left: 10, top: 88, width: 20, height: 8 }, 40)).toBe('top')
  })
})

describe('stepBox', () => {
  it('returns null for a step that was never anchored', () => {
    expect(stepBox(draftWith())).toBeNull()
    expect(stepBox(draftWith({ target: { selectors: [] } }))).toBeNull()
  })

  it('reads the geometry recorded on the target', () => {
    const draft = draftWith({
      target: { bbox: { x: 64, y: 80, w: 128, h: 80, viewport: { w: 640, h: 400 } } },
    })
    expect(stepBox(draft)).toEqual({ left: 10, top: 20, width: 20, height: 20 })
  })
})

describe('screenFor', () => {
  it('prefers the replica when one was captured and has loaded', () => {
    const screen = screenFor(draftWith({ sandboxKey: 'k.json' }), snapshot(), '/shot.png')
    expect(screen).toMatchObject({ kind: 'replica' })
  })

  it('falls back to the screenshot while the replica is still loading', () => {
    const screen = screenFor(draftWith({ sandboxKey: 'k.json' }), undefined, '/shot.png')
    expect(screen).toEqual({ kind: 'screenshot', src: '/shot.png' })
  })

  it('uses the screenshot for tours recorded before replicas existed', () => {
    expect(screenFor(draftWith(), undefined, '/shot.png')).toEqual({
      kind: 'screenshot',
      src: '/shot.png',
    })
  })

  it('reports an empty screen for a hand-authored step', () => {
    expect(screenFor(draftWith(), undefined, null)).toEqual({ kind: 'empty' })
  })
})
