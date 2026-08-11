import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import { dir, formatNumber, getLocale, registerCatalog, setLocale, t, tParts } from './index'
import { DEFAULT_LOCALE, localeDirection, LOCALES, normalizeLocale } from './locales'
import { resolveLocale } from './resolve'

const CATALOG_DIR = join(__dirname, '../../public/i18n')

// Only English is bundled; in the browser the rest arrive over the network.
// Tests read them off disk so they assert against the catalogs we actually ship.
beforeAll(() => {
  for (const { code } of LOCALES) {
    if (code === DEFAULT_LOCALE) continue
    registerCatalog(code, JSON.parse(readFileSync(join(CATALOG_DIR, `${code}.json`), 'utf8')))
  }
})

afterEach(() => setLocale(DEFAULT_LOCALE))

describe('normalizeLocale', () => {
  it.each([
    ['en', 'en'],
    ['EN', 'en'],
    ['de-DE', 'de'],
    ['de_AT', 'de'],
    ['pt', 'pt-BR'],
    ['pt-PT', 'pt-BR'],
    ['zh', 'zh-CN'],
    ['zh-Hans', 'zh-CN'],
    ['ar-EG', 'ar'],
  ])('canonicalises %s → %s', (raw, expected) => {
    expect(normalizeLocale(raw)).toBe(expected)
  })

  it.each([null, undefined, '', '   ', 'klingon', 'xx', '!!', '1'])('rejects %s', (raw) => {
    expect(normalizeLocale(raw as string | null)).toBeNull()
  })
})

describe('t', () => {
  it('returns the active locale’s string', () => {
    setLocale('de')
    expect(t('header.close')).toBe('Schließen')
  })

  it('interpolates', () => {
    expect(t('help.no_results', { query: 'refund' })).toContain('refund')
  })

  it('leaves an unknown placeholder visible instead of blanking it', () => {
    expect(t('help.no_results')).toContain('{{query}}')
  })

  it('returns the key itself when nothing matches, rather than empty text', () => {
    expect(t('no.such.key')).toBe('no.such.key')
  })

  it('falls back to English rather than showing a raw key', () => {
    setLocale('ja')
    // Every shipped locale resolves every key today; assert the mechanism by
    // checking a locale never renders the key itself.
    for (const key of ['header.close', 'composer.send', 'csat.submit']) {
      expect(t(key)).not.toBe(key)
    }
  })

  describe('plurals follow the locale’s own CLDR rules', () => {
    it('English: one vs other', () => {
      expect(t('csat.star', { count: 1 })).toBe('1 star')
      expect(t('csat.star', { count: 3 })).toBe('3 stars')
    })

    it('Polish: one / few / many are three different words', () => {
      setLocale('pl')
      expect(t('csat.star', { count: 1 })).toBe('1 gwiazdka')
      expect(t('csat.star', { count: 3 })).toBe('3 gwiazdki')
      expect(t('csat.star', { count: 5 })).toBe('5 gwiazdek')
      // 12–14 are `many` even though they end in 2–4 — the classic Polish trap.
      expect(t('csat.star', { count: 12 })).toBe('12 gwiazdek')
      expect(t('csat.star', { count: 22 })).toBe('22 gwiazdki')
    })

    it('Arabic: uses the zero/one/two forms', () => {
      setLocale('ar')
      expect(t('csat.star', { count: 0 })).toBe('لا نجوم')
      expect(t('csat.star', { count: 1 })).toBe('نجمة واحدة')
      expect(t('csat.star', { count: 2 })).toBe('نجمتان')
    })

    it('Japanese: one form for every count', () => {
      setLocale('ja')
      expect(t('csat.star', { count: 1 })).toBe('星 1 つ')
      expect(t('csat.star', { count: 5 })).toBe('星 5 つ')
    })
  })
})

describe('tParts', () => {
  it('splits around the placeholder so markup can go between the halves', () => {
    const [before, after] = tParts('action.wants_to', 'action')
    expect(before).toBe('The assistant wants to ')
    expect(after).toBe('')
  })

  it('lets a translation put the emphasised value first', () => {
    // Turkish phrases it as "Asistan şunu yapmak istiyor: <action>", Japanese
    // trails the verb — both must survive without the component knowing.
    setLocale('ja')
    const [before, after] = tParts('page_assist.allow', 'page')
    expect(before).toBe('')
    expect(after).toBe(' でアシスタントに操作を任せる')
  })

  it('falls back to the whole string when the placeholder is absent', () => {
    const [before, after] = tParts('header.close', 'nope')
    expect(before).toBe('Close')
    expect(after).toBe('')
  })
})

describe('direction', () => {
  it('is rtl only for Arabic', () => {
    setLocale('ar')
    expect(dir()).toBe('rtl')
    setLocale('de')
    expect(dir()).toBe('ltr')
  })

  it('resolves regional tags before deciding', () => {
    expect(localeDirection('ar-EG')).toBe('rtl')
    expect(localeDirection('klingon')).toBe('ltr')
  })
})

describe('formatNumber', () => {
  it('uses the locale’s separators', () => {
    setLocale('de')
    expect(formatNumber(1234.5)).toBe('1.234,5')
    setLocale('en')
    expect(formatNumber(1234.5)).toBe('1,234.5')
  })
})

describe('setLocale', () => {
  it('normalises and falls back to the default for junk', () => {
    expect(setLocale('de-CH')).toBe('de')
    expect(setLocale('klingon')).toBe(DEFAULT_LOCALE)
    expect(getLocale()).toBe(DEFAULT_LOCALE)
  })
})

describe('resolveLocale', () => {
  it('prefers what the visitor writes over everything else', () => {
    // The whole point: an en-US browser on a German site, but this person
    // types Turkish. They get Turkish.
    expect(
      resolveLocale({
        learned: 'tr',
        explicit: 'de',
        browser: ['en-US'],
        workspaceDefault: 'de',
      }),
    ).toBe('tr')
  })

  it('honours the host’s setting when there is nothing learned yet', () => {
    expect(resolveLocale({ explicit: 'de', browser: ['en-US'] })).toBe('de')
  })

  it('falls back to the browser, then the workspace default, then English', () => {
    expect(resolveLocale({ browser: ['fr-CA', 'en'] })).toBe('fr')
    expect(resolveLocale({ browser: ['klingon'], workspaceDefault: 'it' })).toBe('it')
    expect(resolveLocale({})).toBe(DEFAULT_LOCALE)
  })

  it('skips browser languages we do not ship', () => {
    expect(resolveLocale({ browser: ['is-IS', 'sw', 'nl-BE'] })).toBe('nl')
  })

  it('lets a host lock the language against the visitor’s evidence', () => {
    expect(resolveLocale({ explicit: 'de', learned: 'tr', lockLocale: true })).toBe('de')
  })

  it('ignores lockLocale when the locked locale is not one we ship', () => {
    expect(resolveLocale({ explicit: 'klingon', learned: 'tr', lockLocale: true })).toBe('tr')
  })
})

describe('catalog coverage', () => {
  it('every shipped locale renders real copy for a representative key', () => {
    for (const { code } of LOCALES) {
      setLocale(code)
      expect(t('composer.placeholder')).not.toBe('composer.placeholder')
    }
  })
})

describe('lazy catalog loading', () => {
  // A fresh module instance: only English inlined, nothing else registered yet.
  async function freshModule() {
    vi.resetModules()
    return import('./index')
  }

  afterEach(() => vi.unstubAllGlobals())

  it('only English is bundled', async () => {
    const i18n = await freshModule()
    expect(i18n.hasCatalog('en')).toBe(true)
    expect(i18n.hasCatalog('de')).toBe(false)
  })

  it('fetches a catalog from the widget asset path and uses it', async () => {
    const i18n = await freshModule()
    const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({ 'header.close': 'X' }) }))
    vi.stubGlobal('fetch', fetchMock)

    await i18n.ensureCatalog('de', 'https://api.example.com/')
    expect(fetchMock).toHaveBeenCalledWith(
      'https://api.example.com/widget-assets/i18n/de.json',
      expect.anything(),
    )
    i18n.setLocale('de')
    expect(i18n.t('header.close')).toBe('X')
  })

  it('falls back to English rather than throwing when the fetch fails', async () => {
    const i18n = await freshModule()
    vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new Error('offline'))))

    // Must not reject — this runs inside the customer's own page.
    await expect(i18n.ensureCatalog('de', 'https://api.example.com')).resolves.toBeUndefined()
    i18n.setLocale('de')
    expect(i18n.t('header.close')).toBe('Close')
  })

  it('falls back to English on a 404 (e.g. a locale dropped by a deploy)', async () => {
    const i18n = await freshModule()
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, json: async () => ({}) })))

    await i18n.ensureCatalog('ja', 'https://api.example.com')
    i18n.setLocale('ja')
    expect(i18n.t('composer.send')).toBe('Send message')
  })

  it('does not fetch the same locale twice', async () => {
    const i18n = await freshModule()
    const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({}) }))
    vi.stubGlobal('fetch', fetchMock)

    await Promise.all([
      i18n.ensureCatalog('fr', 'https://api.example.com'),
      i18n.ensureCatalog('fr', 'https://api.example.com'),
    ])
    await i18n.ensureCatalog('fr', 'https://api.example.com')
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('never fetches English, which is already inlined', async () => {
    const i18n = await freshModule()
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    await i18n.ensureCatalog('en', 'https://api.example.com')
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
