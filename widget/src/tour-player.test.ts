import { describe, expect, it } from 'vitest'

import type { Tour } from './types'
import { computeTooltipPosition, resolveAutoPlacement, selectFirstEligibleTour } from './tour-player'

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
})
