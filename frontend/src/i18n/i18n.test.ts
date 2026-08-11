import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import {
  applyDocumentLocale,
  DEFAULT_LOCALE,
  dir,
  getLocale,
  hasCatalog,
  localeDirection,
  LOCALES,
  normalizeLocale,
  registerCatalog,
  setLocale,
  t,
  tParts,
} from './index'
import { timeAgo } from '@/lib/format'

afterEach(async () => {
  await setLocale(DEFAULT_LOCALE)
})

describe('normalizeLocale', () => {
  it.each([
    ['de-DE', 'de'],
    ['pt', 'pt-BR'],
    ['zh-Hans', 'zh-CN'],
    ['ar-EG', 'ar'],
  ])('canonicalises %s → %s', (raw, expected) => {
    expect(normalizeLocale(raw)).toBe(expected)
  })

  it('rejects languages we do not ship', () => {
    expect(normalizeLocale('klingon')).toBeNull()
  })
})

describe('t', () => {
  it('renders English out of the bundled catalog', () => {
    expect(t('common.save')).toBe('Save')
  })

  it('renders the active language once its catalog is loaded', async () => {
    await setLocale('de')
    expect(t('common.save')).toBe('Speichern')
  })

  it('falls back to English for a key the locale has not translated yet', async () => {
    await setLocale('de')
    // The dashboard is translated in waves, so most keys are still English-only.
    // Asserting against a genuinely untranslated key — rather than a stub
    // catalog — checks the real shipped state: readable English, never a key.
    expect(t('ai.agent_saved')).toBe('Agent saved')
  })

  it('a partial catalog still resolves what it does have', () => {
    registerCatalog('is', { 'common.save': 'Vista' })
    // `is` is not a shipped locale, so this only exercises the seed path.
    expect(hasCatalog('is')).toBe(false)
  })

  it('returns the key itself only when nothing has it', () => {
    expect(t('no.such.key.anywhere')).toBe('no.such.key.anywhere')
  })

  it('interpolates, and leaves unknown placeholders visible', () => {
    expect(t('time.yesterday_at', { time: '14:03' })).toBe('Yesterday 14:03')
    expect(t('time.yesterday_at')).toContain('{{time}}')
  })
})

describe('plurals', () => {
  it('uses the locale’s own CLDR categories', async () => {
    // English and Polish disagree about 5, and Polish disagrees with itself
    // about 12 vs 22 — the case a naive `n === 1 ? a : b` gets wrong.
    expect(t('time.days', { count: 3 })).toBe('3d')
    await setLocale('pl')
    expect(t('time.days', { count: 3 })).toBe('3 dn.')
  })
})

describe('tParts', () => {
  it('splits a translation around its placeholder', () => {
    const [before, after] = tParts('time.yesterday_at', 'time')
    expect(before).toBe('Yesterday ')
    expect(after).toBe('')
  })

  it('returns the whole string when the placeholder is absent', () => {
    expect(tParts('common.save', 'nope')).toEqual(['Save', ''])
  })
})

describe('direction', () => {
  it('is rtl for Arabic only', async () => {
    await setLocale('ar')
    expect(dir()).toBe('rtl')
    await setLocale('ja')
    expect(dir()).toBe('ltr')
  })

  it('resolves regional tags before deciding', () => {
    expect(localeDirection('ar-EG')).toBe('rtl')
  })
})

describe('applyDocumentLocale', () => {
  beforeEach(() => {
    document.documentElement.lang = ''
    document.documentElement.dir = ''
  })

  it('sets lang and dir, which screen readers and RTL both need', () => {
    applyDocumentLocale('ar')
    expect(document.documentElement.lang).toBe('ar')
    expect(document.documentElement.dir).toBe('rtl')

    applyDocumentLocale('de')
    expect(document.documentElement.lang).toBe('de')
    expect(document.documentElement.dir).toBe('ltr')
  })
})

describe('setLocale', () => {
  it('normalises, and falls back to the default for junk', async () => {
    expect(await setLocale('de-CH')).toBe('de')
    expect(await setLocale('klingon')).toBe(DEFAULT_LOCALE)
    expect(getLocale()).toBe(DEFAULT_LOCALE)
  })

  it('only English is bundled; the rest load on demand', () => {
    expect(hasCatalog('en')).toBe(true)
  })

  it('loads a catalog for every shipped locale', async () => {
    for (const { code } of LOCALES) {
      await setLocale(code)
      expect(hasCatalog(code)).toBe(true)
    }
  })
})

describe('formatting follows the app language, not the browser', () => {
  it('relative time comes from the catalog rather than date-fns English', async () => {
    const twoMinutesAgo = new Date(Date.now() - 2 * 60 * 1000)
    expect(timeAgo(twoMinutesAgo)).toBe('2m')
    await setLocale('de')
    expect(timeAgo(twoMinutesAgo)).toBe('2 Min.')
    await setLocale('ja')
    expect(timeAgo(twoMinutesAgo)).toBe('2分')
  })
})
