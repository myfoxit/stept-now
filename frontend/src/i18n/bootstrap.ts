/**
 * Choosing the dashboard's language at startup, and keeping it chosen.
 *
 * Three sources, strongest first:
 *
 * 1. the account's stored `locale` — an explicit choice, which must survive
 *    logging in from a colleague's laptop;
 * 2. a copy of that choice cached in `localStorage` — needed because the
 *    account is not known until `/me` resolves, and rendering the login screen
 *    in the wrong language for two seconds is exactly the kind of flicker that
 *    reads as broken;
 * 3. `navigator.languages`, for a browser that has never signed in here.
 *
 * The cache is a convenience, never the authority: `applyUserLocale` overwrites
 * it the moment the server tells us what the account actually prefers.
 */

import { DEFAULT_LOCALE, normalizeLocale, setLocale } from './index'

const CACHE_KEY = 'stept-locale'

function cached(): string | null {
  try {
    return normalizeLocale(localStorage.getItem(CACHE_KEY))
  } catch {
    return null // private mode, disabled storage — fall through to the browser
  }
}

function cache(locale: string | null): void {
  try {
    if (locale) localStorage.setItem(CACHE_KEY, locale)
    else localStorage.removeItem(CACHE_KEY)
  } catch {
    /* not worth failing a language change over */
  }
}

function fromBrowser(): string | null {
  if (typeof navigator === 'undefined') return null
  const tags = navigator.languages?.length ? navigator.languages : [navigator.language]
  for (const tag of tags) {
    const code = normalizeLocale(tag)
    if (code) return code
  }
  return null
}

/**
 * Pick and apply a language before the first render.
 *
 * Awaited in `main.tsx`: the catalog has to be in memory before React paints,
 * or the whole app renders once in English and then repaints, which is both
 * ugly and a layout-shift problem in RTL.
 */
export async function bootstrapLocale(): Promise<string> {
  return setLocale(cached() ?? fromBrowser() ?? DEFAULT_LOCALE)
}

/**
 * Apply the locale the signed-in account chose.
 *
 * A null `locale` means "follow the browser" — an account that has never picked
 * one, which is not the same as an account that picked English.
 */
export async function applyUserLocale(locale: string | null | undefined): Promise<string> {
  const chosen = normalizeLocale(locale)
  cache(chosen)
  return setLocale(chosen ?? fromBrowser() ?? DEFAULT_LOCALE)
}
