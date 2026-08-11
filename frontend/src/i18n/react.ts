/**
 * React bindings for the translation runtime.
 *
 * `useSyncExternalStore` rather than a context provider: the locale is genuinely
 * module-global (one language per window), and a provider would force every test
 * that renders a component in isolation to wrap it in one. This way `t` works
 * the same in a component, in an event handler, and in a plain helper function.
 */

import { useCallback, useSyncExternalStore } from 'react'

import { dir, getLocale, setLocale, snapshot, subscribe, t, tParts } from './index'

export interface Translation {
  /** Translate a key. Re-renders the calling component when the language changes. */
  t: typeof t
  /** Split a translation around a placeholder so a component can sit between the halves. */
  tParts: typeof tParts
  /** The active locale code. */
  locale: string
  /** Layout direction for the active locale. */
  dir: 'ltr' | 'rtl'
  /** Switch language; resolves once the new catalog is in memory. */
  setLocale: (locale: string) => Promise<string>
}

/** Subscribe this component to the active language. */
export function useTranslation(): Translation {
  useSyncExternalStore(subscribe, snapshot, snapshot)
  return {
    t,
    tParts,
    locale: getLocale(),
    dir: dir(),
    setLocale,
  }
}

/**
 * Locale-aware `Intl` formatters, memoised on the active language.
 *
 * Number and date formatting has to follow the *app's* language, not the
 * browser's — those disagree the moment someone picks a language in settings,
 * and "1,234.5" inside an otherwise German page is a bug people notice.
 */
export function useFormatters() {
  const { locale } = useTranslation()

  const number = useCallback(
    (value: number, options?: Intl.NumberFormatOptions) =>
      new Intl.NumberFormat(locale, options).format(value),
    [locale],
  )

  const date = useCallback(
    (value: string | Date, options?: Intl.DateTimeFormatOptions) =>
      new Intl.DateTimeFormat(locale, options).format(
        typeof value === 'string' ? new Date(value) : value,
      ),
    [locale],
  )

  return { number, date, locale }
}
