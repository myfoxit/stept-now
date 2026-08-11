/**
 * Deciding which language to show a visitor.
 *
 * The ordering here is the opinionated part. Most chat widgets read
 * `navigator.language` and stop, which gets the common case right and the
 * interesting case wrong: someone on a borrowed laptop, a shared kiosk, or a
 * browser they never changed from the factory `en-US` types a question in
 * Turkish and gets answered in Turkish — inside an English interface.
 *
 * So the strongest signal is what the visitor actually *writes*. The server
 * detects the language of their messages and stores it on the contact
 * (`learned`); from the next render on, the whole widget follows it. Nothing
 * else is direct evidence about this human — a browser header is evidence about
 * a machine, and a site-wide setting is evidence about the site.
 *
 * A host that genuinely needs a fixed interface language (a regulated
 * single-language product, a screenshot harness) sets `lockLocale: true` and
 * `explicit` wins outright.
 */

import { DEFAULT_LOCALE, normalizeLocale } from './locales'

export interface LocaleSources {
  /** `window.SteptSettings.locale` — the host page's explicit choice. */
  explicit?: string | null
  /** Language this visitor writes in, detected server-side and stored on the contact. */
  learned?: string | null
  /** `navigator.languages`, best-first. */
  browser?: readonly string[] | null
  /** The workspace's default, from the boot response. */
  workspaceDefault?: string | null
  /** Host opt-out: honour `explicit` even against evidence from the visitor. */
  lockLocale?: boolean
}

export function resolveLocale(sources: LocaleSources): string {
  const explicit = normalizeLocale(sources.explicit)
  if (sources.lockLocale && explicit) return explicit

  const learned = normalizeLocale(sources.learned)
  if (learned) return learned

  if (explicit) return explicit

  for (const tag of sources.browser ?? []) {
    const code = normalizeLocale(tag)
    if (code) return code
  }

  return normalizeLocale(sources.workspaceDefault) ?? DEFAULT_LOCALE
}

/** `navigator.languages` with a `navigator.language` fallback, safe in tests/SSR. */
export function browserLanguages(): readonly string[] {
  if (typeof navigator === 'undefined') return []
  const list = navigator.languages
  if (Array.isArray(list) && list.length > 0) return list
  return navigator.language ? [navigator.language] : []
}
