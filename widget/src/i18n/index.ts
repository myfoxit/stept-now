/**
 * Widget translation runtime — deliberately ~150 lines rather than i18next.
 *
 * This bundle is injected into someone else's page, so every kilobyte is rent
 * we charge our customers' visitors. What i18next would add over this is
 * namespaces, backends, and a plugin system; what this needs is lookup,
 * interpolation, and plurals. `Intl.PluralRules` supplies correct CLDR
 * categories for free, so the widget matches the backend's hand-written rules
 * without shipping a rules table.
 *
 * **Only English is bundled.** Inlining all thirteen catalogs cost +18 KB
 * gzipped on both the app and the loader — paid by every visitor, in twelve
 * languages they cannot read. Instead the other locales are static assets under
 * `/widget-assets/i18n/`, fetched once when they are actually needed (~1 KB
 * gzipped each). English is inlined so the first paint, the loading screen, and
 * every error path work with no network at all, and so a failed fetch degrades
 * to readable English rather than raw keys.
 *
 * Catalog format is identical to the backend's and the dashboard's: flat dotted
 * keys, `{{name}}` interpolation, `key_one`/`key_other` plural siblings.
 *
 * The locale is module state rather than a Preact context because the
 * controller already owns app state and re-renders on change; a context would
 * add a provider to every test that mounts a component in isolation.
 */

import en from '../../public/i18n/en.json'
import { DEFAULT_LOCALE, localeDirection, normalizeLocale } from './locales'

export type Catalog = Record<string, string>

/** Loaded catalogs. English is present from the first tick; others arrive later. */
const CATALOGS: Record<string, Catalog> = { en }

let current: string = DEFAULT_LOCALE

/**
 * Set the active locale. Returns the locale actually applied.
 *
 * Synchronous by design: callers switch language immediately and text falls
 * back to English for the moment before `ensureCatalog` resolves, rather than
 * blocking a render on a network round trip.
 */
export function setLocale(raw: string | null | undefined): string {
  current = normalizeLocale(raw) ?? DEFAULT_LOCALE
  return current
}

export function getLocale(): string {
  return current
}

/** Seed a catalog directly — used by tests and by the loader→app handoff. */
export function registerCatalog(locale: string, catalog: Catalog): void {
  const code = normalizeLocale(locale)
  if (code) CATALOGS[code] = catalog
}

export function hasCatalog(locale: string): boolean {
  const code = normalizeLocale(locale)
  return code !== null && CATALOGS[code] !== undefined
}

const inflight = new Map<string, Promise<void>>()

/**
 * Fetch `locale`'s catalog if it is not loaded yet.
 *
 * Never rejects: a locale we cannot fetch (offline, blocked, 404 after a bad
 * deploy) leaves the visitor reading English, which is a far better outcome
 * than an unhandled rejection inside someone else's page.
 */
export function ensureCatalog(locale: string, assetBase: string): Promise<void> {
  const code = normalizeLocale(locale)
  if (!code || CATALOGS[code]) return Promise.resolve()

  const existing = inflight.get(code)
  if (existing) return existing

  const base = assetBase.replace(/\/+$/, '')
  const request = fetch(`${base}/widget-assets/i18n/${code}.json`, { credentials: 'omit' })
    .then((res) => (res.ok ? res.json() : null))
    .then((data: unknown) => {
      if (data && typeof data === 'object' && !Array.isArray(data)) {
        CATALOGS[code] = data as Catalog
      }
    })
    .catch(() => {
      /* stay on English */
    })
    .finally(() => inflight.delete(code))

  inflight.set(code, request)
  return request
}

/** Layout direction for the active locale — drives `dir` on the widget root. */
export function dir(): 'ltr' | 'rtl' {
  return localeDirection(current)
}

const pluralRules = new Map<string, Intl.PluralRules>()

function categoryFor(locale: string, count: number): string {
  let rules = pluralRules.get(locale)
  if (!rules) {
    try {
      rules = new Intl.PluralRules(locale)
    } catch {
      rules = new Intl.PluralRules(DEFAULT_LOCALE)
    }
    pluralRules.set(locale, rules)
  }
  return rules.select(count)
}

function interpolate(template: string, vars?: Record<string, unknown>): string {
  if (!vars) return template
  // An unknown placeholder is left visible rather than blanked: the sentence
  // still reads, and the gap is obvious in a screenshot.
  return template.replace(/\{\{\s*(\w+)\s*\}\}/g, (whole, name: string) =>
    name in vars ? String(vars[name]) : whole,
  )
}

function lookup(key: string, vars?: Record<string, unknown>): string | null {
  const count = vars?.count
  const candidates: string[] = []
  if (typeof count === 'number' && Number.isFinite(count)) {
    candidates.push(`${key}_${categoryFor(current, count)}`, `${key}_other`)
  }
  candidates.push(key)

  // `current` first, then English — a not-yet-fetched or half-finished
  // translation shows English, never a raw key.
  for (const locale of current === DEFAULT_LOCALE ? [current] : [current, DEFAULT_LOCALE]) {
    const catalog = CATALOGS[locale]
    if (!catalog) continue
    for (const candidate of candidates) {
      const template = catalog[candidate]
      if (template !== undefined) return interpolate(template, vars)
    }
  }
  return null
}

/** Translate `key` in the active locale. Unknown keys render as the key itself. */
export function t(key: string, vars?: Record<string, unknown>): string {
  return lookup(key, vars) ?? key
}

/**
 * Split a translation around one placeholder, for strings that wrap a value in
 * markup: `The assistant wants to <strong>{{action}}</strong>`.
 *
 * Returning the two halves lets the caller render the element between them, so
 * word order stays the translator's decision — in Japanese and Turkish the
 * emphasised noun lands before the verb, which a hardcoded
 * `<span>{prefix}<strong>{x}</strong></span>` could never express.
 */
export function tParts(
  key: string,
  placeholder: string,
  vars?: Record<string, unknown>,
): [string, string] {
  const token = `{{${placeholder}}}`
  const raw = lookup(key, { ...vars, [placeholder]: token }) ?? key
  const at = raw.indexOf(token)
  if (at === -1) return [raw, '']
  return [raw.slice(0, at), raw.slice(at + token.length)]
}

/** Locale-aware number formatting (thousands separators differ by language). */
export function formatNumber(value: number): string {
  try {
    return new Intl.NumberFormat(current).format(value)
  } catch {
    return String(value)
  }
}

export {
  DEFAULT_LOCALE,
  LOCALES,
  localeDirection,
  normalizeLocale,
  SUPPORTED_LOCALES,
} from './locales'
export type { Locale, LocaleInfo } from './locales'
