/**
 * Hand-rolled i18n for the marketing site — no runtime, no dependency.
 *
 * Catalogs are flat dot-notation JSON (same format as the dashboard's
 * `frontend/src/i18n/catalogs/`). English is the source of truth; a missing
 * key in any other locale falls back to English at build time, so the site
 * builds (and localized routes render English) before a translation wave
 * lands.
 */
import en from './en.json'
import de from './de.json'
import fr from './fr.json'
import es from './es.json'
import it from './it.json'

export const locales = ['en', 'de', 'fr', 'es', 'it'] as const
export type Locale = (typeof locales)[number]

export const defaultLocale: Locale = 'en'

export const localeMeta: Record<Locale, { nativeName: string; htmlLang: string }> = {
  en: { nativeName: 'English', htmlLang: 'en' },
  de: { nativeName: 'Deutsch', htmlLang: 'de' },
  fr: { nativeName: 'Français', htmlLang: 'fr' },
  es: { nativeName: 'Español', htmlLang: 'es' },
  it: { nativeName: 'Italiano', htmlLang: 'it' },
}

type Catalog = Record<string, string>

const catalogs: Record<Locale, Catalog> = { en, de, fr, es, it }

/**
 * Look a key up in `locale`'s catalog, falling back to English, then to the
 * key itself. `{{placeholder}}` tokens are replaced from `params`; unknown
 * placeholders are left as-is.
 */
export function t(
  locale: Locale,
  key: string,
  params?: Record<string, string | number>,
): string {
  const raw = catalogs[locale][key] ?? catalogs[defaultLocale][key] ?? key
  if (!params) return raw
  return raw.replace(/\{\{(\w+)\}\}/g, (match, name: string) =>
    name in params ? String(params[name]) : match,
  )
}

/** The path of `path`'s version in `locale`: `/privacy` → `/de/privacy`, `/` → `/de/`. */
export function localePath(locale: Locale, path: string = '/'): string {
  const clean = path.startsWith('/') ? path : `/${path}`
  if (locale === defaultLocale) return clean
  return clean === '/' ? `/${locale}/` : `/${locale}${clean}`
}

/** Split a pathname into its locale and its locale-free path (`/de/privacy` → de + `/privacy`). */
export function stripLocale(pathname: string): { locale: Locale; path: string } {
  for (const locale of locales) {
    if (locale === defaultLocale) continue
    if (pathname === `/${locale}` || pathname === `/${locale}/`) return { locale, path: '/' }
    if (pathname.startsWith(`/${locale}/`)) {
      return { locale, path: pathname.slice(locale.length + 1) }
    }
  }
  return { locale: defaultLocale, path: pathname }
}
