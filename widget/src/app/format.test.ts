import { afterEach, describe, expect, it } from 'vitest'

import { DEFAULT_LOCALE, setLocale } from '../i18n'
import { clockTime, timeAgo, trimPreview } from './format'

afterEach(() => setLocale(DEFAULT_LOCALE))

describe('trimPreview', () => {
  it('returns short text untouched', () => {
    expect(trimPreview('Hello there')).toBe('Hello there')
  })

  it('cuts at a word boundary, never mid-word', () => {
    const long =
      'Die Einrichtung deiner Wissensdatenbank beginnt mit dem ersten Dokument im Portal'
    expect(trimPreview(long)).toBe('Die Einrichtung deiner Wissensdatenbank beginnt mit dem…')
  })

  it('collapses whitespace and strips trailing punctuation before the ellipsis', () => {
    expect(trimPreview('One   two\n\nthree', 8)).toBe('One two…')
    expect(trimPreview('Well, that is a question, is it not, my dear friend?', 24)).toBe(
      'Well, that is a…',
    )
  })

  it('hard-cuts a single giant token rather than overflowing', () => {
    const url = 'https://example.com/a/very/long/path/that/never/ends/and/keeps/going/forever'
    const out = trimPreview(url, 30)
    expect(out.length).toBeLessThanOrEqual(31)
    expect(out.endsWith('…')).toBe(true)
  })
})

describe('clockTime follows the widget locale', () => {
  // Build the timestamp from local time so the assertion is timezone-proof.
  const iso = new Date(2026, 7, 11, 14, 5).toISOString()

  it('renders 24h clock for German', () => {
    setLocale('de')
    expect(clockTime(iso)).toBe('14:05')
  })

  it('renders 12h clock for English', () => {
    setLocale('en')
    expect(clockTime(iso)).toMatch(/2:05\sPM/)
  })
})

describe('timeAgo date fallback follows the widget locale', () => {
  it('renders the month in the active language past a week', () => {
    const old = new Date(2026, 0, 15, 12, 0)
    const now = new Date(2026, 7, 11, 12, 0).getTime()
    setLocale('de')
    const de = timeAgo(old.toISOString(), now)
    expect(de).toContain('Jan')
    setLocale('ja')
    expect(timeAgo(old.toISOString(), now)).toContain('1月')
  })
})
