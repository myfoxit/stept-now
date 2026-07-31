import { describe, expect, it } from 'vitest'

import { formatBytes, initials, timeAgo } from '@/lib/format'

describe('format helpers', () => {
  it('initials picks first letters of up to two words', () => {
    expect(initials('Ada Lovelace')).toBe('AL')
    expect(initials('plato')).toBe('P')
    expect(initials('Anne Marie Smith')).toBe('AM')
  })

  it('formatBytes scales units', () => {
    expect(formatBytes(512)).toBe('512 B')
    expect(formatBytes(2048)).toBe('2.0 KB')
    expect(formatBytes(5 * 1024 * 1024)).toBe('5.0 MB')
  })

  it('timeAgo compacts unit names', () => {
    const twoMinutesAgo = new Date(Date.now() - 2 * 60 * 1000)
    expect(timeAgo(twoMinutesAgo)).toBe('2m')
  })
})
