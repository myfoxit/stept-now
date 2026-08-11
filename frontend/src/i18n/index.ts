/**
 * Dashboard translation runtime.
 *
 * Same catalog format and the same ~150-line engine as the widget, the
 * extension and the backend: flat dotted keys, `{{name}}` interpolation,
 * `key_one`/`key_other` plural siblings. That is deliberate — a string moved
 * between the agent UI and the messenger should not need rewriting, and one
 * `check-i18n` guard should be able to police all four.
 *
 * **Why not i18next.** It would earn its ~40 KB on namespaces, backends and a
 * plugin system; this app needs lookup, interpolation and plurals, and gets
 * correct CLDR categories from `Intl.PluralRules` for free. Its `<Trans>`
 * component is the one real loss, and `tParts` covers that case without
 * teaching every developer a second templating language. Not adding a
 * dependency also keeps the repo's "no new dependencies" rule intact.
 *
 * English is bundled so the app renders instantly and a failed chunk load
 * degrades to English rather than raw keys. The other twelve are dynamic
 * imports, which Vite code-splits into their own chunks — a German user
 * downloads German, nobody downloads Japanese.
 */

import en from './catalogs/en.json'
import { DEFAULT_LOCALE, localeDirection, normalizeLocale } from './locales'

export type Catalog = Record<string, string>

const CATALOGS: Record<string, Catalog> = { en }

/**
 * Vite resolves this glob at build time, one chunk per locale.
 *
 * English is excluded by the negative pattern: it is statically imported above,
 * and a module that is both static and dynamic cannot be code-split anyway —
 * it is the synchronous fallback, so it has to be in the main chunk.
 */
const LOADERS = import.meta.glob<Catalog>(['./catalogs/*.json', '!./catalogs/en.json'], {
  import: 'default',
})

let current: string = DEFAULT_LOCALE

// --- subscription (so React can re-render on a language change) ------------

const listeners = new Set<() => void>()

export function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

function notify(): void {
  listeners.forEach((listener) => listener())
}

/**
 * A value that changes whenever the language does.
 *
 * `useSyncExternalStore` needs a snapshot it can compare by identity. The
 * locale string alone is not enough: a catalog arriving over the network
 * changes what `t` returns without changing the locale, and the UI has to
 * repaint for that too.
 */
let version = 0
export function snapshot(): string {
  return `${current}:${version}`
}

// --- catalogs --------------------------------------------------------------

export function getLocale(): string {
  return current
}

export function hasCatalog(locale: string): boolean {
  const code = normalizeLocale(locale)
  return code !== null && CATALOGS[code] !== undefined
}

/** Seed a catalog directly — used by tests. */
export function registerCatalog(locale: string, catalog: Catalog): void {
  const code = normalizeLocale(locale)
  if (!code) return
  CATALOGS[code] = catalog
  version += 1
  notify()
}

const inflight = new Map<string, Promise<void>>()

/**
 * Load `locale`'s catalog if it is not in memory yet.
 *
 * Never rejects. A chunk that fails to load (a stale deploy, a flaky network)
 * leaves the user reading English, which is recoverable; an unhandled rejection
 * during a language switch is not.
 */
export function ensureCatalog(locale: string): Promise<void> {
  const code = normalizeLocale(locale)
  if (!code || CATALOGS[code]) return Promise.resolve()

  const existing = inflight.get(code)
  if (existing) return existing

  const loader = LOADERS[`./catalogs/${code}.json`]
  if (!loader) return Promise.resolve()

  const request = loader()
    .then((catalog) => {
      CATALOGS[code] = catalog
      version += 1
    })
    .catch(() => {
      /* stay on English */
    })
    .finally(() => inflight.delete(code))

  inflight.set(code, request)
  return request
}

/**
 * Switch language: loads the catalog, applies it, and updates `<html>`.
 *
 * The locale is applied *after* its catalog resolves, so the UI never flashes
 * a half-translated frame on the way between two languages.
 */
export async function setLocale(raw: string | null | undefined): Promise<string> {
  const code = normalizeLocale(raw) ?? DEFAULT_LOCALE
  await ensureCatalog(code)
  current = code
  applyDocumentLocale(code)
  notify()
  return code
}

/** Set `<html lang>` and `<html dir>` — screen readers and RTL both need these. */
export function applyDocumentLocale(locale: string): void {
  if (typeof document === 'undefined') return
  const code = normalizeLocale(locale) ?? DEFAULT_LOCALE
  document.documentElement.lang = code
  document.documentElement.dir = localeDirection(code)
}

export function dir(): 'ltr' | 'rtl' {
  return localeDirection(current)
}

// --- lookup ----------------------------------------------------------------

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
  // An unknown placeholder stays visible: the sentence still reads, and the
  // gap is obvious in a screenshot.
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
 * markup or a component: `Deleting {{name}} cannot be undone`.
 *
 * Returning both halves keeps word order the translator's decision, which a
 * hardcoded `<>{prefix}<b>{x}</b></>` cannot.
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

export {
  DEFAULT_LOCALE,
  LOCALES,
  localeDirection,
  normalizeLocale,
  SUPPORTED_LOCALES,
} from './locales'
export type { Locale, LocaleInfo } from './locales'
