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
    ['fr-CA', 'fr'],
    ['es-419', 'es'],
    ['it-CH', 'it'],
  ])('canonicalises %s → %s', (raw, expected) => {
    expect(normalizeLocale(raw)).toBe(expected)
  })

  it.each(['klingon', 'pt', 'zh-Hans', 'ja', 'ar'])('rejects %s, which we do not ship', (raw) => {
    expect(normalizeLocale(raw)).toBeNull()
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

  it('falls back to English key by key when a catalog is partial', async () => {
    // Shipped catalogs are complete, so a partial one has to be seeded: this
    // pins the behaviour a half-loaded or half-translated catalog would get.
    registerCatalog('es', { 'common.save': 'Guardar' })
    await setLocale('es')
    expect(t('common.save')).toBe('Guardar')
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
    // English and French disagree about 0: French groups it with 1 ("0 jour"),
    // the case a naive `n === 1 ? a : b` gets wrong.
    registerCatalog('fr', { stars_one: '{{count}} étoile', stars_other: '{{count}} étoiles' })
    await setLocale('fr')
    expect(t('stars', { count: 0 })).toBe('0 étoile')
    expect(t('stars', { count: 2 })).toBe('2 étoiles')
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
  it('is ltr for every shipped locale, and for junk', async () => {
    // No RTL locale ships today; the mechanism stays for the day one returns.
    for (const { code } of LOCALES) {
      expect(localeDirection(code)).toBe('ltr')
    }
    expect(localeDirection('klingon')).toBe('ltr')
    expect(dir()).toBe('ltr')
  })

  it('resolves regional tags before deciding', () => {
    expect(localeDirection('de-AT')).toBe('ltr')
  })
})

describe('applyDocumentLocale', () => {
  beforeEach(() => {
    document.documentElement.lang = ''
    document.documentElement.dir = ''
  })

  it('sets lang and dir, which screen readers and RTL both need', () => {
    applyDocumentLocale('de')
    expect(document.documentElement.lang).toBe('de')
    expect(document.documentElement.dir).toBe('ltr')

    applyDocumentLocale('it-CH')
    expect(document.documentElement.lang).toBe('it')
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
  })
})
