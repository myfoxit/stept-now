/**
 * The locales Stept ships. Mirrors `backend/app/core/i18n.py`; `scripts/check-i18n.mjs`
 * fails the build if the two drift.
 */

export interface LocaleInfo {
  code: string
  /** What speakers call the language — a picker listing "German" is useless to someone who only reads German. */
  nativeName: string
  englishName: string
  dir: 'ltr' | 'rtl'
}

export const LOCALES: readonly LocaleInfo[] = [
  { code: 'en', nativeName: 'English', englishName: 'English', dir: 'ltr' },
  { code: 'de', nativeName: 'Deutsch', englishName: 'German', dir: 'ltr' },
  { code: 'fr', nativeName: 'Français', englishName: 'French', dir: 'ltr' },
  { code: 'es', nativeName: 'Español', englishName: 'Spanish', dir: 'ltr' },
  { code: 'it', nativeName: 'Italiano', englishName: 'Italian', dir: 'ltr' },
] as const

export type Locale = (typeof LOCALES)[number]['code']

export const DEFAULT_LOCALE = 'en'

export const SUPPORTED_LOCALES: readonly string[] = LOCALES.map((l) => l.code)

/**
 * Tags whose base language alone would resolve wrongly or not at all.
 * Empty since the shipped set shrank to five base-language locales; the
 * mechanism stays for the day a regional locale (pt-BR, zh-CN, …) returns.
 */
const ALIASES: Record<string, string> = {}

/**
 * Canonicalise a BCP-47 tag to a shipped locale, or null.
 *
 * Falls back from region to base language (`de-AT` → `de`) before giving up,
 * so a visitor in Austria gets German rather than English.
 */
export function normalizeLocale(raw: string | null | undefined): string | null {
  if (!raw) return null
  const tag = String(raw).trim().replace(/_/g, '-')
  if (!tag || !/^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{1,8})*$/.test(tag)) return null
  const lowered = tag.toLowerCase()

  const exact = SUPPORTED_LOCALES.find((code) => code.toLowerCase() === lowered)
  if (exact) return exact
  if (ALIASES[lowered]) return ALIASES[lowered]

  const parts = lowered.split('-')
  while (parts.length > 1) {
    parts.pop()
    const candidate = parts.join('-')
    const match = SUPPORTED_LOCALES.find((code) => code.toLowerCase() === candidate)
    if (match) return match
    if (ALIASES[candidate]) return ALIASES[candidate]
  }
  return null
}

export function localeDirection(code: string): 'ltr' | 'rtl' {
  const normalized = normalizeLocale(code) ?? DEFAULT_LOCALE
  return LOCALES.find((l) => l.code === normalized)?.dir ?? 'ltr'
}
