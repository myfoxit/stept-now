import { describe, expect, it } from 'vitest'

import { compactNumber, formatMinutes, formatRate } from './lib'

describe('compactNumber', () => {
  it('leaves small numbers as-is', () => {
    expect(compactNumber(0)).toBe('0')
    expect(compactNumber(999)).toBe('999')
  })
  it('compacts thousands and millions', () => {
    expect(compactNumber(1000)).toBe('1K')
    expect(compactNumber(12900)).toBe('12.9K')
    expect(compactNumber(4_200_000)).toBe('4.2M')
  })
})

describe('formatRate', () => {
  it('renders a whole-number percent', () => {
    expect(formatRate(0.732)).toBe('73%')
    expect(formatRate(1)).toBe('100%')
  })
  it('handles null/undefined', () => {
    expect(formatRate(null)).toBe('—')
    expect(formatRate(undefined)).toBe('—')
  })
})

describe('formatMinutes', () => {
  it('formats minutes, hours and days', () => {
    expect(formatMinutes(42)).toBe('42m')
    expect(formatMinutes(130)).toBe('2.2h')
    expect(formatMinutes(2880)).toBe('2d')
  })
  it('handles null', () => {
    expect(formatMinutes(null)).toBe('—')
  })
})
